# Filing Facts

A GCP-native data pipeline that ingests UK Companies House iXBRL accounts, extracts
financial facts with an LLM, and scores that extraction against the XBRL tags that ship
inside the same documents. The XBRL tags are the ground truth, so the evaluation numbers are
real rather than vibes.

This repository is a public portfolio project built on public data. Design rationale and the
staged build plan are in [PROJECT-BRIEF.md](PROJECT-BRIEF.md).

**Status: Stage 1 complete and running.** One Cloud Run Job fetches one daily Companies House
accounts ZIP, stores it unmodified in Cloud Storage, and records the run in BigQuery.
Cloud Scheduler triggers it each publication morning. Nothing is parsed or extracted yet.

## Roadmap

Each stage is independently shippable and lands as its own pull request.

| Stage | Scope | Status |
|---|---|---|
| 1 | Ingestion: daily ZIP to Cloud Storage, run ledger in BigQuery, Terraform, CI | Done |
| 2 | Parse iXBRL filings, strip tags to plain text, quarantine table, dbt staging models | Next |
| 3 | LLM extraction on Vertex AI against a Pydantic schema, confidence handling, local Airflow | Planned |
| 4 | Evaluation harness: extracted facts scored against XBRL ground truth, model comparison | Planned |
| 5 | Pub/Sub event channels, backfill DAG, observability and alerting | Planned |
| 6 | RAG over filing text and an MCP server for natural-language queries | Planned |

The evaluation results table will replace this section at the top of the README once Stage 4
lands. Until then, everything below describes Stage 1 only.

## How Stage 1 works

```
Cloud Scheduler (08:00 Europe/London, Tue-Sat)
      |  OAuth-authenticated POST to jobs.run
      v
Cloud Run Job: ingest  (1 vCPU, 1 GiB, plain Python)
      |  1. ledger says already succeeded for today's file?  -> exit 0, no download
      |  2. stream ZIP to disk, hash it, check Content-Length and size cap
      |  3. validate the ZIP central directory and every member CRC
      |  4. create-only upload to GCS (if_generation_match=0)
      |  5. batch-load one run row to BigQuery with a deterministic job id
      v
gs://<project>-raw/companies_house/accounts_daily/Accounts_Bulk_Data-YYYY-MM-DD.zip
<project>.filing_facts_raw.ingest_runs
```

Every outcome is recorded, including failure. The ledger row for a `failed` run carries the
exception type and message. Nothing is dropped silently.

### Idempotency, and why it holds

The document id for Stage 1 is the source filename. Three independent mechanisms stop a replay
from duplicating anything:

1. The job checks the ledger for a `succeeded` row before downloading.
2. The GCS write uses a generation precondition, so a second writer cannot overwrite.
3. The BigQuery write is a batch load job whose job id is derived from the source key.
   BigQuery rejects duplicate job ids, so even two runs racing past check 1 append one row.

If a run dies after the GCS write and before the ledger write, the next run re-downloads,
compares hashes with the stored object, and records success only if they match. A mismatch is
recorded as a failure and left for a human. The test suite exercises each of these paths;
see [tests/test_idempotency.py](tests/test_idempotency.py).

### What is deliberately not here

No Cloud Composer, no Dataflow, no Apache Beam, no streaming inserts. One daily ZIP fits on one
small machine, and a single-developer project does not need a managed control plane billing
by the hour. Airflow arrives in Stage 3, running locally in Docker. See section 11 of the brief.

## Prerequisites

Manual steps that sit outside Terraform by design. Everything else is code.

1. **A GCP project with billing enabled.** Terraform never creates or deletes the project.
2. **A budget alert on the billing account.** Budgets attach to the billing account, not the
   project, so they are set by hand in Billing > Budgets & alerts. This project runs with an
   alert at GBP 1 and treats any email from it as a bug.
3. **Local tools:** `gcloud`, Terraform 1.9+, Docker, `uv`, `make`, `gitleaks` (optional).
   ```bash
   brew install --cask google-cloud-sdk docker
   brew install hashicorp/tap/terraform uv gitleaks
   ```
4. **Authenticate once.** Terraform and the local job both use Application Default Credentials.
   ```bash
   gcloud auth login
   gcloud auth application-default login
   gcloud config set project filing-facts-gb
   gcloud auth configure-docker europe-west2-docker.pkg.dev
   ```

## Run it locally with no GCP at all

```bash
uv sync --frozen
make run-local                     # today's file into ./data/raw, ledger in ./data/ingest_runs.jsonl
make run-local ARGS="--date 2026-09-01"
make run-local                     # replay: exits 0 without downloading
```

`--dry-run` swaps the GCS and BigQuery backends for filesystem ones with the same contract,
including create-only writes and ledger dedupe. Do not run this against many dates: the brief
limits development to one daily ZIP.

The same entrypoint runs in Docker:

```bash
make docker-run
```

## Deploy Stage 1

Order matters because of two chicken-and-egg constraints: the remote-state bucket cannot be
created by the Terraform that stores state in it, and a Cloud Run Job cannot exist before its
image does.

```bash
export PROJECT=filing-facts-gb

make bootstrap-apply   # local state: enable APIs, create state bucket and Artifact Registry
make push              # build linux/amd64 image and push it
make apply             # remote state: bucket, dataset, table, SAs, IAM, job, scheduler
```

Then trigger one execution by hand and inspect the ledger:

```bash
gcloud run jobs execute ingest --region europe-west2 --project $PROJECT --wait
bq query --project_id=$PROJECT --location=europe-west2 --nouse_legacy_sql \
  'SELECT source_key, status, byte_count, sha256, finished_at FROM filing_facts_raw.ingest_runs ORDER BY finished_at DESC'
```

Run the execute command twice. The second run logs `already_ingested` and the table still has
one `succeeded` row.

The `infra/bootstrap` state file is local and gitignored. It holds two resource ids and no
secrets. If it is lost, `terraform import` both resources rather than recreating them.

## Tear down

```bash
make destroy           # everything in infra/stage1, raw bucket contents included
```

The bootstrap root is left standing on purpose: the state bucket has versioning and
`force_destroy = false`, so destroying it is a deliberate manual `terraform destroy` in
`infra/bootstrap` after confirming nothing else references it.

## Quality gates

CI runs on every push: gitleaks over full history, Ruff lint and format, Pyright strict,
pytest, `terraform fmt` and `validate` for both roots, and a Docker build. Locally:

```bash
make check             # everything except gitleaks
make secrets           # gitleaks over git history
```

## Cost

Computed, not typed: [docs/cost.md](docs/cost.md) is generated by
`scripts/estimate_cost.py` from measured file sizes and list prices. Regenerate with
`make cost`.

## Known limitations of the source

- Only electronically filed accounts appear in the bulk product, roughly 60 to 75 percent of
  annual filings. Paper and scanned filings, and all revised accounts, are absent.
- Files are named for their publication date, not the filing date, and are published Tuesday
  to Saturday around 06:45 UTC. A 404 for a date is recorded as `not_published`, not a failure.
- Companies House blanks some author metadata fields for security.

## Repository layout

```
src/filing_facts/
  config.py               FF_* environment settings
  sources/companies_house.py   URL scheme, streaming download, ZIP validation
  storage/protocols.py    RawStore and RunLog contracts
  storage/{gcs,bigquery}.py    production backends
  storage/{memory,local}.py    test and --dry-run backends
  ingest/job.py           the Stage 1 job
  ingest/__main__.py      CLI entrypoint
tests/                    idempotency, download failure, truncation, local backends
infra/bootstrap/          APIs, state bucket, Artifact Registry (local state)
infra/stage1/             bucket, dataset, table, IAM, Cloud Run Job, Scheduler (GCS state)
scripts/estimate_cost.py  generates docs/cost.md
```

## Licence

MIT. See [LICENSE](LICENSE).
