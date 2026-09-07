# Project context

This is a public portfolio project. It is NOT connected to my employer and must
never contain employer code, schemas, config, data or architecture.

## What we are building
A GCP-native data pipeline that ingests UK Companies House iXBRL accounts,
extracts financial facts with an LLM, and evaluates that extraction against the
XBRL tags as ground truth.

Full specification: PROJECT-BRIEF.md. Build order is stage by stage (section 5).
Only build the stage that has been explicitly approved.

## Non-negotiables
- Python 3.12. Ruff and Pyright strict must pass before any commit.
- Every pipeline stage must be independently runnable and testable locally.
- Failures go to a quarantine table keyed by document ID. Never silently drop a record.
- All loads idempotent on document ID. Replaying a batch must not duplicate rows.
- All infrastructure in Terraform. No resource created by hand in the console.
- Every number that appears in the README is computed from the repo at build time.
- No secrets in the repo. Use Secret Manager and Workload Identity.

## Cost discipline
- Free trial credit (GBP 220.76) expires 1 December 2026. Budget alert is currently
  set at GBP 1 (the brief's target is GBP 20). Assume I will notice every pound.
- Ask before enabling any GCP API that has a cost implication.
- Prefer Cloud Run Jobs over Dataflow, and local Airflow over Cloud Composer,
  unless the task specifically calls for the managed service.
- BigQuery: batch loads only, never streaming inserts (streaming is charged).
- Billing labels on every Vertex AI call.
- Never download more than one daily Companies House ZIP during development.

## Locked decisions (PROJECT-BRIEF.md section 11) — do not reopen

| Need | DECIDED | Rejected |
|---|---|---|
| Scheduling, stages 1-2 | Cloud Scheduler + Cloud Run Jobs | Cloud Composer |
| Orchestration, stage 3+ | Apache Airflow running locally in Docker | Cloud Composer |
| Batch processing | Cloud Run Jobs, plain Python | Dataflow / Apache Beam |
| Warehouse | BigQuery (free tier, batch loads) | - |
| LLM extraction | Vertex AI (Gemini Flash tier), billed via the project | Standalone Gemini API |
| IaC | Terraform, GCS remote state | Console clicks |

Do not propose Cloud Composer, Dataflow or Apache Beam at any point in this project.
The only exception is an optional 3-4 day Composer stand-up after Stage 4, decided
by me, not proposed by the agent.

## Working style
- Propose a plan before writing code for any new stage. Wait for approval.
- Small commits with clear messages. One concern per commit.
- Write the test before the fix when handling a bug.
- Tell me when something I asked for is a bad idea.

## Before opening any PR
- Run the test suite three times in a row. A test that passes twice and fails once is a bug.
- Run a code review pass over the diff and act on it before requesting review.
- Append every bug, wrong assumption or process failure to `docs/corrections.md`, including
  ones caught before merge. The log is part of the deliverable.

## Pull requests
- One PR per stage for Stage 1; one PR per concern from Stage 2 onwards.
- Title: `<area>: <what changed>` in plain language. No nicknames from the brief.
- Body follows `.github/pull_request_template.md`. Motivation before mechanics, net
  change only, verification with commands, no local paths, no hand-typed numbers.

## Stage-end checklist
- Does `terraform destroy` leave nothing behind?
- Does the stage run from a clean clone with only the README?
- Is there a test that fails if the stage regresses?
- Is the cost of one full run written down somewhere?
