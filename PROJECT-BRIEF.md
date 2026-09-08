# PROJECT BRIEF — "Filing Facts": a GCP-native, agent-assisted data pipeline

**Purpose:** a public portfolio project that closes the exact gaps between my CV and
what London AI Engineer roles are asking for. Built solo, on public data, on the
same stack I operate at work but on a completely different problem.

---

## 1. Why this project, specifically

Requirements pulled from live London AI Engineer postings (Aug 2026), ranked by how
often they appear:

| Requirement | Appears in | Do I have public evidence? |
|---|---|---|
| Production Python, not prototypes | All | Partly (SwitchCards is Node) |
| Evaluations / measuring agent quality | Nearly all | Yes — SwitchCards |
| Agent orchestration + tool calling | Nearly all | Yes — SwitchCards |
| RAG over a real corpus | Most | **No** |
| Monitoring / observability for AI systems | Most | Partly |
| Guardrails, hallucination risk | Most | Yes — SwitchCards |
| Structured outputs, schema validation | Several | Yes — SwitchCards |
| GCP services hands-on (BigQuery, Vertex, Dataflow, Composer) | Several | **No** |
| Docker / Kubernetes | Several | **No** |
| Terraform / CI/CD | Several | **No** |
| MCP tool servers | Named explicitly by one | Partly |

This project is scoped to fill every row marked **No**, and to put the
partly-covered rows into Python on GCP.

---

## 2. The idea

**Extract structured facts from unstructured UK company filings using an LLM, and
evaluate the extraction against ground truth that is already published.**

The trick that makes this work: Companies House publishes BOTH the unstructured
filing documents AND structured data for many of the same facts. That gives a free,
objective ground-truth set. So the eval harness measures real extraction accuracy,
not vibes. Almost no portfolio project can do that, and evaluation is the single
most-requested skill in the postings.

**Why this dataset:**
- Genuinely public and free (Companies House bulk data + document API).
- Messy in interesting ways: scanned PDFs, inconsistent formats, decades of drift.
- Ground truth available for scoring.
- Adjacent to my domain expertise without touching my employer's systems, data,
  code, or architecture. Different problem, same muscles.

**Alternative if I want distance from company data entirely:** UK public procurement
(Contracts Finder + Find a Tender). Same shape: unstructured notices, structured
award records as partial ground truth.

---

## 3. Architecture

> **Superseded, 2026-09-08.** This is the original sketch from before Stage 1. The
> managed services in it (Cloud Composer, Dataflow, Firestore) were rejected in
> section 11 and never built; the EXTRACT step became a Cloud Run Job, not a service;
> run state lives in BigQuery ledger tables, not Firestore. The as-built architecture
> is the diagram in [README.md](README.md#architecture). The sketch is kept because
> the patterns it names (two event channels, quarantine and backfill, idempotent
> loads) are the ones that were built.

Deliberately mirrors patterns from serious production systems: two planes, split
event channels, quarantine and backfill, idempotent loads.

```
CONTROL PLANE
  Cloud Composer (Airflow)
        |  schedules acquire; drains lifecycle events
        v
  Cloud Run Job: ACQUIRE  ---> Cloud Storage (raw filings)
        |                      Firestore (run metadata, cursors)
        |
        |  "batch ready" event
        v
  Pub/Sub: lifecycle channel  (one message per batch)
        |
        v
  Dataflow / Cloud Run Job: PARSE
        |  text extraction, OCR fallback for scans
        |  unparseable docs -> quarantine table
        |
        |  one event per document
        v
  Pub/Sub: per-document channel  (high volume)
        |
        v
  Cloud Run service: EXTRACT  (Vertex AI / Gemini)
        |  structured output against a Pydantic schema
        |  low-confidence or schema-fail -> quarantine, never silent-drop
        v
  BigQuery: raw -> staging -> marts  (dbt)
        |
        +--> EVAL harness: extracted vs Companies House structured data
        +--> MCP server: natural-language queries over the marts
```

### Stack (chosen to close the gaps above)

- **Language:** Python 3.12, Pydantic for schemas, FastAPI for services
- **Orchestration:** Cloud Composer (Airflow)
- **Compute:** Cloud Run Jobs + Services, Dataflow for the batch parse
- **Eventing:** Pub/Sub, two channels at different granularity
- **Storage:** Cloud Storage (raw), Firestore (run state), BigQuery (warehouse)
- **Modelling:** dbt with tests
- **AI:** Vertex AI (Gemini), structured outputs, billing labels for cost attribution
- **RAG:** BigQuery vector search or Vertex AI Vector Search over filing text
- **IaC:** Terraform, environment-scoped remote state
- **CI/CD:** GitHub Actions, Ruff, Pyright strict, pytest
- **Containers:** Docker; Cloud Run natively, optional GKE deployment to evidence K8s
- **Observability:** structlog JSON logs, OpenTelemetry traces, Cloud Monitoring,
  queue-age and dead-letter alerts
- **Interface:** MCP server exposing query tools, usable from Claude Code

---

## 4. The parts that make it portfolio-grade

Anyone can build an ETL job. These are the bits that make an interviewer lean in:

1. **Eval harness with real ground truth.** Score extraction against published
   structured data. Report precision/recall per field, per document type, per model.
   Publish the numbers, including the bad ones.

2. **Model comparison as a first-class output.** Run the same extraction across
   two or three models. Report accuracy against cost and latency. Postings
   explicitly ask for understanding of "trade-offs in latency, cost, context limits."
   This produces that answer with data.

3. **Prompts as versioned artefacts.** Prompts live in the repo, are tested, and a
   regression suite runs on every change. Directly matches the Annapurna wording.

4. **Two kinds of dead letter.** Bus dead-letter for malformed messages; row-level
   quarantine for schema and extraction failures. Then a backfill DAG that
   re-processes only the quarantined keys after a fix.

5. **Idempotent loads.** Stable document key, at-least-once delivery, replays that
   don't duplicate. Prove it with a test that replays the same batch twice.

6. **Cost attribution.** Billing labels on every Vertex call so cost per 1,000
   documents extracted is a number in the README, not a shrug.

7. **A README that reads like an incident-ready runbook.** Architecture diagram,
   what breaks and how it's detected, how to backfill, what it costs to run.

---

## 5. Build order (each stage independently shippable)

**Stage 1 — the boring spine.** Terraform for the project skeleton. One Cloud Run
Job that fetches a single Companies House dataset to GCS. Loads to BigQuery. Airflow
schedules it. CI runs lint and tests. *Nothing clever. Just make it real and repeatable.*

**Stage 2 — parse and quarantine.** Text extraction from filings, OCR fallback,
quarantine table for failures, dbt staging models with tests.

**Stage 3 — extraction with Vertex.** Structured output against a Pydantic schema.
Confidence handling. Failures to quarantine, never dropped silently.

**Stage 4 — the eval harness.** This is the one that earns the interviews. Ground
truth join, per-field scoring, published metrics, model comparison.

**Stage 5 — events and scale.** Split the Pub/Sub channels, make loads idempotent,
add the backfill DAG, add observability and alerting.

**Stage 6 — the interface.** RAG over filing text and an MCP server so the whole
thing is queryable in natural language. Demo GIF in the README.

Stages 1 to 4 are the minimum viable portfolio piece. Five and six are what make it
memorable.

---

## 6. What this unlocks on my CV

Once stages 1 to 4 are live and public, these move from "learning" to defensible:
BigQuery, Vertex AI, Cloud Run, Airflow (local, Composer-compatible DAGs), Pub/Sub,
dbt, Terraform, Docker, RAG, LLM evaluation in Python. Dataflow and Cloud Composer
are **not** on that list: both were rejected in section 11 and never built, so they
do not go on the CV. Kubernetes likewise: Cloud Run was sufficient and GKE was not
stood up.

Interview answer it produces: *"I operate this stack in production at work, and
here's a public repo where I built the whole thing myself, end to end, including the
evaluation layer. The extraction accuracy numbers are in the README, including the
fields it does badly on and why."*

---

## 7. Rules for myself

- Public data only. No employer code, schemas, config, data samples, or architecture.
- Ship stage by stage. A working stage 2 beats an abandoned stage 6.
- Publish the failures. The corrections log is what made SwitchCards credible.
- Keep it cheap. Free tier plus small batches; put the running cost in the README.
- Every number in the README computed from the repo, not typed by hand.

---

## 8. REVISED SOURCE STRATEGY (no OCR needed)

**Primary source: Companies House free Accounts Data Product.**
`download.companieshouse.gov.uk` publishes daily ZIPs (Tue-Sat mornings) and monthly
ZIPs covering the previous 12 months. No registration, no API key, free.

Each ZIP contains individual filings as **iXBRL** (`.html`) or XBRL (`.xml`).
Roughly 97% are iXBRL. These are HTML documents with embedded XBRL taxonomy tags,
so they are both human-readable and machine-parseable. **No OCR anywhere in this
project.**

**Known limitation, state it in the README:** only electronically filed accounts are
included, about 60-75% of the ~2.2m annual filings. Paper and scanned filings are
excluded, and revised/amending accounts are paper-only so they never appear. Also
note Companies House nulls out some author metadata fields for security. Owning
these limitations openly is part of the point.

### The eval design this unlocks — better than the original plan

The iXBRL tags ARE the ground truth, and they travel inside the same file as the
text. So:

1. Take a filing. Parse the XBRL facts with **Arelle** (open-source XBRL parser,
   converts iXBRL to CSV/JSON). That is the answer key.
2. Strip all XBRL tags. Render the remaining HTML to plain text. That is the input.
3. Ask the model to extract the same financial facts from the text alone,
   as structured output against a Pydantic schema.
4. Score extraction against the answer key: per-field precision and recall, by
   document type, by filing size, by model.

Zero labelling effort, perfect ground truth, and a benchmark that is genuinely hard
(small-company accounts are formatted wildly inconsistently). This is the centrepiece
of the project — lead the README with the results table.

Extend it once the core works: extract facts that are NOT XBRL-tagged (directors'
report narrative, going-concern statements, related-party mentions). No ground truth
there, so use the SwitchCards pattern: a second model audits the first against the
source text, and disagreements go to a review queue.

### Fallback / alternative sources, in order

If Companies House proves awkward, these have the same shape (unstructured or
semi-structured source, plus a structured answer key):

1. **SEC EDGAR + Financial Statement Data Sets.** SEC publishes both raw filings and
   quarterly structured datasets extracted from their XBRL. Same ground-truth trick,
   US instead of UK, very well documented, no rate-limit pain.
2. **UK Contracts Finder / Find a Tender (OCDS).** Procurement notices with long
   free-text descriptions plus structured award records. Good for extraction plus
   entity matching to Companies House numbers.
3. **Charity Commission register + annual returns.** Structured register, narrative
   trustee reports. Smaller and friendlier than Companies House.
4. **EU Business Registers / OpenSanctions.** Best if the project pivots toward
   entity resolution rather than extraction.

Pick ONE and go deep. Breadth across sources is a worse portfolio signal than depth
on one.

---

## 9. WHAT I NEED BEFORE STARTING

### Accounts and access

| Thing | Needed? | Notes |
|---|---|---|
| Google Cloud account with billing enabled | **Yes** | New accounts get $300 free credit for 90 days. Billing must be on even to use free tier. |
| GCP project | **Yes** | Create a dedicated one so costs and IAM are isolated. |
| Vertex AI / Gemini | **Yes, via GCP** | No separate subscription. Vertex AI is enabled as a GCP API and billed per token through the same project. Do NOT use a personal Gemini app subscription; it is a different product. |
| Companies House account or API key | **No** for bulk | Bulk ZIPs need nothing. A free API key is only needed if I later use the REST API for company metadata. |
| Apache Airflow account | **No such thing** | Airflow is open source. Either run it locally in Docker, or use Cloud Composer, which is a managed GCP service. See the cost warning below. |
| GitHub account | **Yes** | Public repo. This is the deliverable. |
| Terraform Cloud | **No** | Use a GCS bucket for remote state. |

### Local tooling

`gcloud` CLI, Terraform CLI, Docker Desktop, Python 3.12, `uv`, and `dbt-bigquery`.

### COST WARNING — read this before Stage 1

**Cloud Composer is expensive.** The smallest environment runs continuously and costs
roughly £250-350/month. It is not a "spin it up for a weekend project" service and it
does not stop charging when idle.

Recommended approach:
- **Stages 1-4:** run Airflow locally in Docker (`astro dev` or the official compose
  file), or use Cloud Scheduler triggering Cloud Run Jobs. Costs pennies.
- **Stage 5 only:** stand up Cloud Composer for a week to get genuine hands-on
  experience and screenshots, then tear it down with `terraform destroy`.
- Set a **GCP budget alert at £20** on day one, before writing any code.
- Use small batches. One daily ZIP, not twelve monthly ones.
- Put billing labels on every Vertex call from the start so cost-per-1000-documents
  is measurable rather than guessed.

BigQuery, Cloud Storage, Pub/Sub, Cloud Run and Vertex AI are all cheap at this scale.
Composer and Dataflow are the two that can surprise you. Use Dataflow briefly for the
same reason: real experience, then tear down.

---

## 10. INSTRUCTIONS FOR CLAUDE CODE

### Repo setup, do this first

Create the repo, then create `CLAUDE.md` at the root containing:

```markdown
# Project context

This is a public portfolio project. It is NOT connected to my employer and must
never contain employer code, schemas, config, data or architecture.

## What we are building
A GCP-native data pipeline that ingests UK Companies House iXBRL accounts,
extracts financial facts with an LLM, and evaluates that extraction against the
XBRL tags as ground truth.

## Non-negotiables
- Python 3.12. Ruff and Pyright strict must pass before any commit.
- Every pipeline stage must be independently runnable and testable locally.
- Failures go to a quarantine table keyed by document ID. Never silently drop a record.
- All loads idempotent on document ID. Replaying a batch must not duplicate rows.
- All infrastructure in Terraform. No resource created by hand in the console.
- Every number that appears in the README is computed from the repo at build time.
- No secrets in the repo. Use Secret Manager and Workload Identity.

## Cost discipline
- Budget alert set at GBP 20. Assume I will notice every pound.
- Prefer Cloud Run Jobs over Dataflow, and local Airflow over Cloud Composer,
  unless the task specifically calls for the managed service.
- Billing labels on every Vertex AI call.

## Working style
- Propose a plan before writing code for any new stage. Wait for approval.
- Small commits with clear messages. One concern per commit.
- Write the test before the fix when handling a bug.
- Tell me when something I asked for is a bad idea.
```

### Kickoff prompt for Claude Code

```
Read CLAUDE.md and PROJECT-BRIEF.md in this repo.

We are building Stage 1 only: the boring spine. Do not build anything from later
stages, and do not add an LLM yet.

Stage 1 definition of done:
1. Terraform that provisions: a GCS bucket for raw files, a BigQuery dataset,
   a service account with least-privilege IAM, and a GCS bucket for TF remote state.
2. A Python Cloud Run Job that downloads ONE daily Companies House accounts ZIP
   from download.companieshouse.gov.uk, stores it unmodified in the raw bucket,
   and writes a run record (run id, source URL, file hash, byte count, timestamp)
   to BigQuery.
3. The job must be idempotent: running it twice for the same source file must not
   create a second raw object or a second run record.
4. Dockerfile for the job. It must run locally with the same entrypoint.
5. pytest covering: idempotency, a failed download, and a truncated file.
6. GitHub Actions running ruff, pyright and pytest on every push.
7. A README with a setup section someone else could follow from zero.

Constraints:
- Do not create any GCP resource outside Terraform.
- Do not download more than one daily file during development.
- Ask me before enabling any GCP API that has a cost implication.

Start by proposing the repo structure and the Terraform resource list.
Wait for my approval before writing code.
```

### Stage prompts after that

Keep one prompt per stage, each ending with "wait for my approval before writing
code." The staging discipline is what stops an agent building six half-finished
layers instead of one working one.

### What to check at the end of every stage

- Does `terraform destroy` leave nothing behind?
- Does the stage run from a clean clone with only the README?
- Is there a test that fails if the stage regresses?
- Is the cost of one full run written down somewhere?

---

## 11. LOCKED DECISIONS — orchestration and processing

These are settled. Do not reintroduce the paid services during the build.

| Need | DECIDED tool | Rejected | Why |
|---|---|---|---|
| Scheduling, stages 1-2 | Cloud Scheduler + Cloud Run Jobs | Cloud Composer | Only 1-2 tasks at this point. Orchestration would be ceremony. Free tier covers it. |
| Orchestration, stage 3+ | **Apache Airflow running locally in Docker** | Cloud Composer | Identical DAG code. Composer costs ~GBP 350+/month and bills continuously even when idle. |
| Batch processing | Cloud Run Jobs, plain Python | Dataflow / Apache Beam | One daily ZIP, a few thousand filings, fits on one machine. Dataflow bills per vCPU-hour to do a smaller job. |
| Warehouse | BigQuery | - | Free tier: 10 GiB storage, 1 TiB queries/month. Use batch loads, NOT streaming inserts (streaming is charged). |
| LLM extraction | Vertex AI (Gemini Flash tier) | Standalone Gemini API subscription | Vertex is billed through the GCP project. Put billing labels on every call. |
| IaC | Terraform | Console clicks | Trial resources get deleted at trial end. Terraform means `apply` rebuilds everything. |

**Optional, at the very end only:** stand up Cloud Composer for 3-4 days to experience
the managed version and capture screenshots, then `terraform destroy`. Budget ~GBP 40.
Not required. Decide after stage 4.

**Cost guardrails already in place:** budget alert set at GBP 1. Free trial credit
GBP 220.76, expires 1 December 2026. GCP does not auto-charge when a trial ends; the
account pauses and resources are deleted, which is why everything must live in Terraform.

**Interview framing for these choices:** sizing tools to the problem is a seniority
signal. "I used Cloud Run Jobs rather than Dataflow because the batch volume did not
justify a distributed runner, and I ran Airflow locally rather than Composer because a
single-developer project does not need a GBP 350/month managed control plane" is a
better answer than having used the expensive service by default.
