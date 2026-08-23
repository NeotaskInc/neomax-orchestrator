# Neomax Orchestrator — session opener (automatic, and deliberately LEAN)

**You usually don't need to paste anything.** When you start a Neomax orchestrator session
(`neomax` or a provider-pinned `cmax` / `cdxmax` / `ocmax` / `kmax` / `gmax` launcher), the
provider's native startup path injects this orientation directive with
the **engine, mode, and fleet computed live** — so you just type your first message (your task,
or "go") and it orients + leads with a short confirmation. The directive is self-gating: it
fires ONLY in an interactive orchestrator session, never in a plain `claude` session or a
headless `neomax` worker.

## The opener is a toolbox, not a playbook
The original opener baked in hundreds of lines of orchestration mandates (maximal fan-out,
"fifty concurrent workers", engine-routing rules, per-turn delegate-don't-execute nagging).
That made every orchestrator far too aggressive — over-parallelizing, saturating accounts,
splitting work badly. It is gone, and it is not coming back. What the opener carries now:

- **Facts:** orchestrator engine (`NEOMAX_ROLE`), worker-pool scope (`NEOMAX_FLEET`, hard-enforced
  by neomax), configured fleet size (verify live with `neomax status`), and which
  registered project the session is driving (or MULTI-PROJECT mode) with its paths/branch prefix.
- **The TOOLBOX — what the orchestrator can actually drive**, so it never has to guess whether a
  capability exists: its own Agent-tool/Workflow sub-agents · `neomax delegate`
  (`--engine`, `--model`, `-e`, `--goal`, `--plan`, `--pr`) · the `run-all` scheduler · watch/recover
  (`ls`/`status`/`log`/`audit`/`history`/`find`/`reconcile`/`ack`/`clean`) · steer a run
  (`resume`/`retry`/`kill`/`pr`/`shepherd`) · account control (`pause`/`unpause`/`orchestrators`/
  `premerge-check`/`cmax N`/`cdx login N`) · **provider rotation** (`/rotate` / `neomax rotate`
  automatically use the current provider; Claude swaps in place and the other providers hand off
  between isolated profiles, with no new login and a model-free CLI fallback) · the
  per-engine model selection (`--model` / `--ENGINE-model`) · the OpenCode local registry
  (`ocx models`) · account helpers and orchestrator launchers for every engine · backlog +
  the cross-repo issue ledger · usage/cost + the portal. Each entry points at `neomax help`
  (full registry) and `neomax modes` (launch/account cheat-sheet).
- **The LAUNCH-MODE map**, rendered from `ORCH_MODES` (the same source `neomax modes` and the
  dashboard's Quick Actions render, so the views cannot drift): every `cmax` / `cdxmax` / `ocmax` / `kmax` / `gmax`
  variant with its orchestrator + worker scope, plus the account commands. And the fact that
  **scope is fixed at launch** — if you say "only use Codex" mid-session, the orchestrator can't
  flip `NEOMAX_FLEET`; it honors you live by dispatching `--engine codex` and/or
  `neomax pause all --engine claude` (reversible with `unpause`), and asks for a relaunch only
  if the scope needs to WIDEN.
- **Mechanics:** rotation is auto-armed at ≥99% — **Claude** on either the 5-hour or the weekly
  window, **Codex** on weekly only (it has no 5-hour limit any more, so the opener never claims a
  5h trigger for a Codex orchestrator). The usage watcher rescues a rate-limited session; workers
  fail over on usage limits automatically.
- **OpenCode:** `ocmax` defaults to the OpenCode-only pool and OX Alpha. `ocx login N [provider]`
  adds an isolated profile; `ocx models N` lists qualified local-registry IDs. Explicit overrides
  pin all OpenCode agent roles together. Rotation is reactive to structured 429/reset headers.
- **Kimi Code:** `kmax` defaults to K3 and Kimi-only workers. `kmx login N` adds an isolated
  `KIMI_CODE_HOME`; explicit models and the K3/K2.7 aliases are supported.
- **Grok Build:** `gmax` defaults to Grok-only workers. `gmx login N` offers browser OAuth,
  device OAuth, and API-key profiles; `grok-4.6` remains the default.
- **Non-negotiables:** explicit defaults, recorded user-selected model overrides, git safety (push/PR freely, never
  merge to main without operator approval), never end a turn silently with workers in flight.
- **Exactly ONE operating principle:** use sub-agents and workers; anything that can run in
  parallel should, spread **evenly across the available accounts** rather than stacked on one.

### The hard rule: NO NUMBERS in the opener
No worker-count targets, no "max N sub-agents", no caps, no fan-out mandates, no engine-routing
rules. A number in the opener is exactly what made the system over-aggressive before — the
principle says *spread parallel work evenly*, and the orchestrator decides the count from the
work's real seams. `test_orient_directive` enforces this: it asserts the toolbox coverage and
runs a **no-digits** check over the operating-principle paragraph.

**All remaining orchestration STRATEGY — decomposition, what to delegate vs do in-session,
engine routing, briefing standards, review gates — lives in the project's own `CLAUDE.md` /
`AGENTS.md`** (plus any per-directory `AGENTS.md` and the project's registered
`ORCHESTRATOR_OPENER.md` supplement, which neomax appends to the opener). Tune a project's
behavior by editing THOSE files, not the tool.

The per-turn UserPromptSubmit hook is likewise informational only: a one-line live
worker/account count plus where to look things up (`neomax status` / `neomax help`). No
mandates.

## In practice
- **`cmax` (Claude orchestrator, any `--workers` mode):** start it, then just send your task.
  Nothing to paste.
- **`cdxmax` (Codex orchestrator):** Codex doesn't use Claude's hooks, so its brain
  (`AGENTS.md`) tells it to run `neomax orient` as its first action. If it doesn't, type
  `neomax orient` yourself.
- **`ocmax` (OpenCode orchestrator):** receives the live `neomax orient` directive as its
  initial prompt and pins its native subagents to the selected OpenCode model.
- **`kmax` (Kimi orchestrator):** defaults to K3, bootstraps `neomax orient`, and resumes that session
  in the interactive Kimi CLI.
- **`gmax` (Grok orchestrator):** starts or resumes the native Grok Build TUI with the selected model.
- **See it anytime:** run `neomax orient` — it prints the exact live directive for the
  current mode/fleet/project.

The single source of truth is `orient_directive()` in `lib/neomax/orientation.py`; the hook and
`neomax orient` both render from it. To change what every orchestrator session is told,
edit that function — but keep it lean; project-specific guidance belongs in the project's MDs.
