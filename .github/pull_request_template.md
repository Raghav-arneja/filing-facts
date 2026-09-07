## Summary

<!-- One paragraph. What this PR delivers, in plain language. -->

## Motivation

<!-- Why the change is needed. Lead with this; the reader should understand the problem before the solution. -->

## What changed

<!-- Group by area (application, infrastructure, tooling, docs). Describe the net change, not the path taken. -->

## Verification

<!-- New or changed tests and what they prove. Manual checks, with commands. Do not list things CI already enforces. -->

## Deployment and cost

<!-- Has this been applied? What does `terraform plan` show? Any change to docs/cost.md? Rollback path. Delete if not applicable. -->

## Known limitations and follow-ups

<!-- What is deliberately out of scope, and which stage picks it up. -->

## Checklist

- [ ] Ruff, Pyright strict and pytest pass locally
- [ ] Terraform changes reviewed via `plan` before `apply`
- [ ] `docs/cost.md` regenerated if infrastructure or run profile changed
- [ ] No secrets, local paths, or employer material in the diff
