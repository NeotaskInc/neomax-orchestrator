# Contributing to Neomax Orchestrator

Contributions from people and development agents are welcome. Bug reports, provider compatibility
updates, documentation improvements, tests, and focused features all help Neomax improve.

## Before opening an issue

- Search existing issues first.
- Use the bug or feature form and provide the smallest useful reproduction.
- Include the Neomax commit/version, operating system, upstream CLI version, provider, command,
  expected result, and actual result when relevant.
- Redact access tokens, API keys, cookies, OAuth data, account identities, personal home paths,
  private repository names, and proprietary source or logs.
- For a security vulnerability, follow `SECURITY.md` instead of filing a public issue.

## Pull requests

1. Fork `NeotaskInc/neomax-orchestrator` and create a focused branch from `main`.
2. Read `AGENTS.md` and `ARCHITECTURE.md`. Preserve the documented account-selection, quota,
   model, privacy, durable-run, module-registry, and cleanup invariants.
3. Add or update hermetic tests. Do not use a contributor's authenticated accounts in automated
   tests, and do not include real profile state or credentials in fixtures.
4. Run the verification commands from `AGENTS.md` and record the results in the PR template.
5. Append a concise `WORKLOG.md` entry for every pull request, including user-visible changes and
   exact verification. Update `README.md`, provider workflows, and installers when behavior changes.
6. Open a pull request against `main`. Explain the problem, the chosen solution, risks, and proof.

Small, reviewable commits are preferred. A linked issue is helpful for larger changes but is not
required for a clear, self-contained fix.

## Provider changes

A provider integration is a cross-surface contract. Review all of these when changing one:

- authentication and isolated profile discovery
- orchestrator and headless-worker command construction
- resume, native subagents, token/usage extraction, and structured rate-limit evidence
- smart Neomax eligibility and worker-pool selection
- `neomax status --json` and universal portal rendering
- model default, explicit overrides, and recorded effective model
- provider-native `/neomax`, `/rotate`, `/find-issues`, and `/fix-issues` workflows
- installer, uninstaller, README, and hermetic tests

Do not claim live provider support from a dry run alone. If a change requires authenticated proof,
state exactly what was tested and obtain explicit authorization before making the request.

## Review expectations

Maintainers may ask for smaller scope, regression tests, privacy cleanup, or proof against a real
upstream CLI. Be respectful, keep discussion technical, and do not merge another contributor's
work without maintainer approval.
