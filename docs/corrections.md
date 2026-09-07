# Corrections log

Every bug, wrong assumption or process failure found during the build, with what caught it
and what changed. Kept because the brief says to publish the failures, and because a log of
what went wrong is better evidence of engineering judgement than a claim that nothing did.

Entries are appended, never edited. Newest last.

| Date | Stage | What was wrong | How it was caught | What changed |
|---|---|---|---|---|
| 2026-09-02 | 1 | Dockerfile pinned a uv base-image tag that does not exist. | First `docker build`. | Copy the uv binary from its release image into `python:3.12-slim`, pinned to the local uv version. |
| 2026-09-03 | 1 | Assumed daily ZIPs would exhaust the GBP 1 budget alert within a month. | Computing the cost properly in `docs/cost.md`. | Estimate corrected; storage is the only growing line and is pennies. |
| 2026-09-03 | 1 | Misread a different day's file as a CDN inconsistency mid-deploy. | Checking the ledger row's `source_key`. | None in code. Noted here because a wrong alarm is still a mistake. |
| 2026-09-07 | 1 | Rewriting git history to fix commit authorship invalidated the commit SHA the Cloud Run image was tagged with. | Reviewing what else referenced the old SHAs. | Rebuilt and re-applied with the new SHA. Lesson: history rewrites break anything keyed on SHAs; do them before any deploy or tag. |
| 2026-09-07 | 1 | CI ran every job twice per push because the workflow triggered on both `push` and `pull_request`. | Reading the checks tab on PR #1. | `push` trigger scoped to `main`. |
| 2026-09-07 | 2 | Hidden-fact detection used Python `id()` of lxml proxy objects, which are recycled, so the count varied between test runs. | A test that passed once and failed on the next run. | Ancestor walk instead of identity set. Expected count re-derived independently by regex over the raw file. Suite now run three times before any PR. |
| 2026-09-07 | 2 | A test assumed `core:` and `frs-core:` prefixes resolve to different namespace URIs. They resolve to the same one. | The test failing against real fixtures. | Test corrected to assert the actual, stronger property: different prefixes, same identity. |
| 2026-09-07 | 2 | The first dbt agreement test required every occurrence of a fact to carry identical text. 487 groups failed: non-numeric dates appear as `2025-12-31` in the hidden block and `31 December 2025` on the page, the same fact rendered two ways. An earlier integrity query had only checked numeric facts, so the claim "no group disagrees" was narrower than it sounded. | `dbt test` against real data. | Agreement is a numeric property; `stg_facts` keeps the hidden form as `text` and the printed form as `display_text`, and the test covers numeric facts only. |
| 2026-09-07 | 2 | Two row-size constants in the cost script were typed as estimates but commented as measured. | Re-reading the diff before commit. | Replaced with values measured from BigQuery (`LENGTH(TO_JSON_STRING(row))`) and the query recorded in the comment. |
| 2026-09-07 | 2 | `stg_facts` picked `text` and `value` with ANY_VALUE, which is arbitrary among ties, so the evaluation answer key could read differently on successive queries. | Pre-PR code-review pass. | Every pick is an ordered ARRAY_AGG. |
| 2026-09-07 | 2 | The agreement test ignored nil occurrences (COUNT DISTINCT drops NULLs), so a nil plus a valued occurrence passed as consistent; a second test duplicated it. | Pre-PR code-review pass. | Nil counts as a distinct value; the test warns instead of failing the build and `stg_facts.inconsistent` carries the flag forward. |
| 2026-09-07 | 2 | The README said dbt never creates a dataset, but nothing enforced it: the dataset name was assembled in dbt independently of Terraform, and dbt's default behaviour is to create missing datasets. | Pre-PR code-review pass. | `create_schema` is overridden to fail; verified that a wrong dataset name creates nothing. |
| 2026-09-07 | 2 | dbt's telemetry cookie was committed, telemetry was on, and `.gitignore` ignored every `logs/` directory in the repo rather than dbt's. | Pre-PR code-review pass. | File removed and ignored; telemetry off; pattern anchored. |
| 2026-09-07 | 2 | No dbt test ran in CI; `dbt parse` checks YAML and Jinja only, so a renamed column would have passed. | Pre-PR code-review pass, against the stage-end checklist. | CI authenticates through Workload Identity Federation and runs `dbt build` in a Terraform-owned CI dataset. |
| 2026-09-07 | 2 | A dataset grant to the Cloud Run service account was added for a dbt writer that does not exist. | Pre-PR code-review pass. | Removed. The CI identity has its own least-privilege grants. |
