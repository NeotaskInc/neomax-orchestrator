---
description: Find verified defects across the current project's repositories and file deduplicated cross-repo issues
---

You are the Neomax orchestrator in FIND-ISSUES mode for the current project. Confirm the project and its repositories with `neomax projects`. Find real defects, missing edge-case handling, contract drift, and operational gaps across the complete system. Do not fix anything in this mode.

`$ARGUMENTS` may scope this pass to a repository, subsystem, or defect class. With no arguments, sweep broadly but investigate one area deeply enough to produce evidence.

Run one bounded pass per invocation:

1. Reconcile and inspect the current issue ledger:
   ```sh
   neomax issue reconcile
   neomax issue list --json
   ```
   `neomax issue open` deduplicates active issues by project, affected repositories, and normalized title, but still avoid wasting work on known findings.
2. Fan out read-only scouts across independent repositories, subsystems, or defect classes. Choose any enabled engine with `neomax delegate --plan --engine ENGINE auto "<complete scout brief>"`. Cover concrete risks such as null handling, exceptions, races, security, performance, resource leaks, missing tests, and cross-repository API/schema/auth drift. Use native subagents when the current harness supports them.
3. Verify every finding adversarially. Drop speculation, style preferences, duplicates, and anything without a reproducible code path or concrete evidence.
4. File each surviving issue against the repositories it actually affects:
   ```sh
   neomax issue open --title "<specific title>" \
     --body "WHAT: ...\nWHERE: <repo>:<file:line>\nWHY: ...\nEVIDENCE: ...\nAFFECTED REPOS: ...\nSUGGESTED FIX: ..." \
     --repos repo-a,repo-b --project <name>
   ```
   Use `--all` only when the issue genuinely affects the entire project.
5. Report the created or deduplicated issue keys and titles, then stop. Invoke `/find-issues` again, or schedule it with the current harness's native loop feature, for another pass.

Only file verified, specific, actionable defects. Preserve repository instructions and never create duplicate noise.
