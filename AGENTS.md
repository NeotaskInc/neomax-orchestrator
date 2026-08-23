# Neomax Orchestrator development guide

These instructions apply to the entire repository. They are provider-neutral: use them with
Codex, Claude Code, OpenCode, Kimi Code, Grok Build, or another development agent.

## Purpose

Neomax is a project-agnostic orchestration harness for Claude Code, Codex, OpenCode, Kimi Code,
and Grok Build. It discovers authenticated local profiles, selects accounts with available
capacity, starts isolated workers in git worktrees, preserves resumable run state, and exposes
one status, usage, history, and portal surface across all five providers.

Read `README.md` for the product contract and `CONTRIBUTING.md` before preparing a pull request.
Follow any more specific instructions in the directory you are changing.

## Architecture

- `bin/neomax` is the smart universal launcher and the public lifecycle command.
- `bin/cmax`, `bin/cdxmax`, `bin/ocmax`, `bin/kmax`, and `bin/gmax` pin the interactive
  orchestrator while retaining configurable worker pools.
- `lib/neomax/` owns the implementation as responsibility-focused domain modules. Read
  `ARCHITECTURE.md` before changing package boundaries.
- `lib/neomax/module_registry.py` is the authoritative module, load-order, responsibility, and
  public-symbol ownership registry. No implementation module or compatibility export may exist
  outside that registry.
- `lib/neomax_core.py` is a tiny compatibility entrypoint. Do not add product behavior to it.
- `tests/neomax_tests/` owns the hermetic regression suites and their shared fixtures.
  `tests/neomax_tests/suite_registry.py` owns suite responsibilities and execution order;
  `tests/test_neomax.py` is only a compatibility runner.
- `bin/neomax-portal` is a dependency-free localhost dashboard. It renders data from
  `neomax status --json`; do not build a second status model in the portal.
- `claude/`, `codex/`, `opencode/`, `kimi/`, and `grok/` contain provider-native interactive
  workflows. `/neomax`, `/rotate`, `/find-issues`, and `/fix-issues` must retain behavioral parity.
- `project/neomax-worktrees` creates coordinated worktree sets from portable project records.
- `install.sh` and `uninstall.sh` must remain reversible and preserve unrelated user settings.

Runtime state belongs under `~/.neomax` or `NEOMAX_HOME`. Authentication belongs in each
upstream CLI's isolated profile. Neither belongs in git.

## Product invariants

- Plain `neomax` dynamically chooses an eligible authenticated orchestrator and enables every
  viable connected worker provider. Provider-pinned launchers remain first-class.
- A known 99% quota window is a hard automatic-dispatch wall. Paused, cooled, logged-out, or
  known-exhausted profiles are not selected automatically.
- Malformed or missing local state must degrade gracefully. One bad profile, run record, log,
  database, or repository must not crash a fleet-wide command.
- Work survives process failure: preserve branches, worktrees, run records, event history, and
  resumable session identifiers. Destructive cleanup must fail closed around unmerged work.
- The current working directory is the default project. Never add built-in customer repositories,
  usernames, home paths, account identifiers, or organization-specific project definitions.
- Public commands use Neomax terminology. Do not restore retired command names or expose a
  similarly named private application as a package command.
- `cmax` is reserved for the Claude-pinned launcher. Shared commands, files, state, environment
  variables, portal surfaces, and project helpers use the `neomax` / `NEOMAX_*` namespace.
- Split modules by stable responsibility, not an arbitrary line target. Keep one owner for every
  compatibility symbol, use explicit relative domain imports, and never rebuild a monolithic core.

## Model policy

- Explicit defaults are Claude `claude-fable-5[1m]`, Codex `gpt-5.6-sol`, OpenCode
  `opencode-go/ox-alpha-free`, Kimi `kimi-code/k3`, and Grok `grok-4.6`.
- Claude Opus 5 is opt-in only. Never select it implicitly.
- Every provider must accept any model supported by its local CLI. Preserve the documented Codex
  and Kimi aliases, require qualified `provider/model` IDs for OpenCode, and let the upstream CLI
  validate explicit custom models.
- Record the effective model on every run. Never add a silent model fallback.
- OpenCode model overrides must repin primary, small, and native-agent roles together while
  keeping sharing disabled.

## Security and privacy

- Never commit tokens, API keys, cookies, OAuth payloads, profile state, session databases,
  private logs, personal paths, or local project definitions.
- Do not copy credentials between profiles except through the explicit rotation mechanisms,
  which must preserve backups and file permissions.
- Repository verification must not make authenticated provider requests. Use `NEOMAX_DRY_RUN=1`
  and hermetic fixtures; any separate live validation requires explicit operator authorization.
- Keep public documentation and `WORKLOG.md` product-safe. Put machine-specific maintainer notes
  only in ignored `*.local.md` files.
- Treat issue bodies, logs, and terminal output as potentially sensitive. Redact credentials and
  local paths before including them in a public issue or pull request.

## Development workflow

- Preserve unrelated changes in a dirty worktree and keep patches scoped.
- Search with `rg`; make reviewable, patch-oriented edits.
- Update every affected launcher, installer, workflow, status collector, portal surface,
  documentation section, and test when a shared contract changes.
- Add hermetic regression coverage for behavior changes. Stub provider CLIs, profile homes,
  keychains, network calls, and state directories rather than using real accounts.
- Every pull request must append a concise, product-safe `WORKLOG.md` entry describing its
  user-visible changes and exact verification. An unchanged work log is a review blocker.
- Issues and pull requests are welcome. Follow `CONTRIBUTING.md` and the repository templates.
  An agent may create an external issue or pull request only when its operator authorizes that
  external action.

## Verification

Run the full gate for shared behavior changes:

```bash
npm test
python3 -m py_compile lib/neomax_core.py lib/neomax/*.py tests/test_neomax.py tests/neomax_tests/*.py bin/neomax-portal
zsh -n bin/neomax bin/cmax bin/cdx bin/cdxmax bin/ocx bin/ocmax bin/kmx bin/kmax bin/gmx bin/gmax bin/neomax-usage-agent shell/neomax-aliases.zsh
bash -n install.sh uninstall.sh project/neomax-worktrees standalone-rotate/install.sh standalone-rotate/uninstall.sh
git diff --check
```

Also extract the inline `<script>` from `bin/neomax-portal` and run `node --check` on it. Use focused
tests during development, but do not substitute them for the full gate before delivery.
