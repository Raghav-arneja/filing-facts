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
