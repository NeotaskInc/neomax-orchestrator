# Neomax Orchestrator work log

<!--
This public work log intentionally starts with no implementation entries.

Every pull request must append one concise, product-safe entry containing:

## YYYY-MM-DD — concise change title

- User-visible behavior changed.
- Exact verification performed.

Never include credentials, account identities, private repositories, personal paths, or local
operational notes. Maintainer-only context belongs in the ignored WORKLOG.local.md file.
-->

## 2026-09-19 - Move CI to Blacksmith

- Run the existing Linux verification job on Blacksmith Ubuntu 24.04 with four
  vCPUs. Test commands, action versions, permissions and triggers are preserved.
- Verification: Actionlint passed for `.github/workflows/test.yml`; the shared
  runner audit accepts the workflow with no GitHub-hosted fallback;
  `git diff --check` passed. Application code is unchanged.
- Remote CI on the candidate revision remains pending; this source check does
  not establish runner installation access or execution.

## 2026-09-25 - Pin CI actions and keep main runs

- Pin every CI action to a full commit SHA, add a 10-minute job timeout, stop
  persisting the checkout token, and cancel superseded runs only for pull
  requests so every `main` push keeps its CI result. The verification steps
  are unchanged.
- Verification: Actionlint passed for `.github/workflows/test.yml`; the shared
  runner audit reports Blacksmith only; `git diff --check` passed.
