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
| 2026-09-07 | 2 | A 1-in-25 sample survey of number formats missed the hyphenated ixt v4 names (`num-dot-decimal`) present in 2 of 10,288 filings. | The quarantine table, on the first full-day local run. | Hyphens normalised before the format lookup; the two filings now parse. The quarantine-then-fix loop worked as designed. |
| 2026-09-07 | 2 | Parse-job batch id was hashed over the not-yet-processed set, which shrinks on replay once quarantine rows exist, so a crash between the facts and documents loads double-loaded every fact on resume. The crash test passed only because its fixture had no quarantinable member. | Pre-PR code-review pass; reproduced on the real ZIP (261,869 facts loaded twice). | Batches are pinned in the ledger with a `started` row listing their members before any data write; a replay resumes that exact batch. |
| 2026-09-07 | 2 | A BigQuery job-id Conflict was treated as "already loaded". BigQuery reserves the ids of failed jobs too, so a failed load blocked its batch forever and the replay reported success with no data. | Pre-PR code-review pass. | On Conflict the prior job's state is inspected; an errored job is retried under a numbered suffix. |
| 2026-09-07 | 2 | Unrecognised ZIP member names were re-quarantined on every replay because only recognised members were checked against the processed set. | Pre-PR code-review pass; reproduced. | Processed-ness is tracked by member name for every member, recognised or not. |
| 2026-09-07 | 2 | The local JSONL sink created its batch marker before appending rows, so a crash in between left an empty batch marked done. | Pre-PR code-review pass; reproduced. | One file per batch, written to a temp path and renamed into place atomically. |
| 2026-09-07 | 2 | The job held a whole batch in memory; a full day measured 2.39 GB peak against a 1 GiB Cloud Run job. | Pre-PR code-review pass, measured with the real ZIP. | Rows spool to NDJSON on disk as produced and BigQuery loads from the file. Full day now peaks at 84 MB. |
| 2026-09-07 | 2 | Raising the parse cap had no effect on the scheduled path, contradicting the config docstring, and a lost success-ledger write left a source pending forever. | Pre-PR code-review pass. | Pending is judged against the cap recorded on the last success; pinned batches make the lost-write case converge. |
