# Neomax Orchestrator development guide

`AGENTS.md` is the canonical provider-neutral development guide for this repository. Read and
follow it in full; this file keeps the same product invariants visible to Claude Code sessions.

This repository is a project-agnostic multi-account orchestration harness for Claude Code,
Codex, OpenCode, Kimi Code, and Grok Build. It runs isolated, resumable workers in git worktrees and exposes one
shared status, history, usage, and portal surface.

If `CLAUDE.local.md` exists, read it after `AGENTS.md` and this file. It is an ignored maintainer override
for machine- or project-specific context and must never be committed.

## Components

- `bin/neomax`: smart universal orchestrator launcher.
- `bin/cmax`, `bin/cdxmax`, `bin/ocmax`, `bin/kmax`, `bin/gmax`: provider-pinned orchestrator launchers.
- `lib/neomax/`: responsibility-focused implementation package. Its authoritative module and
  export registry is `lib/neomax/module_registry.py`; see `ARCHITECTURE.md`.
- `lib/neomax_core.py`: compatibility entrypoint only; product behavior does not belong here.
- `tests/neomax_tests/`: registry-driven, responsibility-focused regression suites;
  `tests/test_neomax.py` is a compatibility runner only.
- `bin/neomax-portal`: dependency-free localhost dashboard rendering `neomax status --json`.
- `bin/cdx`, `bin/ocx`, `bin/kmx`, `bin/gmx`: account helpers.
- `project/neomax-worktrees`: coordinated worktree sets for registered multi-repo projects.
- `install.sh`, `uninstall.sh`: reversible per-user installation.

State lives under `~/.neomax` unless `NEOMAX_HOME` overrides it. Account credentials
remain in each upstream CLI's normal isolated profile and must never enter this repository.

## Project behavior

The directory where an orchestrator launcher starts is the active project. Neomax registers it
locally in `~/.neomax/projects.json`; tracked code contains no built-in customer, company,
repository, username, or home-directory definitions. Project rules come from that project's
own `CLAUDE.md`, `AGENTS.md`, and optional `docs/neomax-orchestrator/ORCHESTRATOR_OPENER.md`.

Local seed definitions may be placed in ignored `project/projects.local.json`. The tracked
`project/projects.example.json` documents the portable schema.

## Development rules

- Preserve unrelated work in a dirty worktree.
- Use `rg` for repository searches and `apply_patch` for edits.
- Keep `neomax status --json` as the portal's source of truth.
- Keep every implementation module and exported compatibility symbol in the authoritative module
  registry. Split by ownership rather than an arbitrary line count; do not recreate a monolith.
- Keep every regression test in exactly one suite-registry entry and preserve the explicit execution
  order when tests share compatibility state.
- Degrade gracefully on malformed or missing state.
- Never store tokens or copy authentication between profiles.
- Repository verification must never make authenticated provider requests. Use hermetic fixtures
  and `NEOMAX_DRY_RUN=1`; separate live validation requires explicit operator authorization.
- Run `npm test`, Python compilation, shell syntax checks, portal JavaScript `node --check`,
  and `git diff --check` for behavior changes.

## Model policy

- Explicit defaults: Claude `claude-fable-5[1m]`, Codex `gpt-5.6-sol`, OpenCode
  `opencode-go/ox-alpha-free`, Kimi `kimi-code/k3`, and Grok `grok-4.6`.
- Every engine accepts any model supported by its local CLI via `--model` or its
  `--ENGINE-model` flag. Preserve Codex and Kimi short aliases. Claude Opus 5 is opt-in only.
- OpenCode overrides must be qualified `provider/model` IDs from `opencode models`.
  Repin the primary, small, and every built-in/native agent together; keep sharing disabled.
- Record the effective model on every run and never add silent model fallback.

Every pull request must append a concise, public-safe `WORKLOG.md` entry covering the change and
exact verification; an unchanged work log is a review blocker. Put private operational context in
`WORKLOG.local.md`.
