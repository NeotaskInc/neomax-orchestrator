---
name: neomax
description: Orchestrate isolated, resumable workers across Claude, Codex, OpenCode, Kimi, and Grok
---

Delegate work to headless Claude, Codex, OpenCode, Kimi, or Grok workers across the enabled account pool. `neomax delegate auto` picks the least-busy, eligible, logged-in account for the selected engine. Each worker is a fresh process with no access to this conversation, isolated in its own git worktree on a `neomax/<id>` branch, watchdog-protected, failed over when supported quota evidence requires it, and resumable by session ID. Workers commit their work; you bring their branches together deliberately.

Task to delegate: $ARGUMENTS

## 0. Before anything — recover state

Run `neomax ls`. Handle anything unfinished before starting new work: `neomax resume <id>` (continue with context), `neomax retry <id>` (fresh attempt, another account), `neomax kill <id>` (orphaned — worker still alive after its engine died; kill before touching it), `neomax ack <id>` (you've received the result) / `neomax clean <id>` (after merging). The SessionStart hook surfaces a COMPLETION INBOX of finished-but-unacknowledged runs every session — never leave one unresolved. `neomax reconcile` = what each outstanding run needs; `neomax audit [id]` = full event timeline. Never leave work stalled, unacknowledged, or forgotten.

**Agent affinity — reuse context before spawning fresh.** Before delegating work related to something already done, run `neomax find <path|keyword>`. If a prior run touched that area, prefer `neomax resume <id> "<new related task>"` (reuses that worker's FULL context — ideal for follow-ups) or route the new task to the same account that worker used. Avoids re-deriving context and keeps related work coherent.

## 0b. The delegation brief — HOW you hand work off (the single most important thing)

You are the active Neomax orchestrator. Your highest-leverage work is planning, decomposition, and precise briefing. A worker that gets "fix the latency issue" must rediscover the task; a worker that receives the evidence, boundaries, approach, and acceptance checks can execute it directly. Never send a vague one-liner. Neomax warns on a thin prompt (`--brief` acknowledges a genuinely trivial one).

**Every worker brief is a self-contained mini-spec** because the worker cannot see this conversation or your plan:

| Section | What goes in it |
|---|---|
| **OBJECTIVE** | The exact end state — not "improve X" but "X returns within 150ms p95; the N+1 in `list.ts` is gone." |
| **CONTEXT / WHY** | The background + root cause you already know. What's broken, where, why it matters, what you've ruled out. Give the worker your understanding. |
| **SCOPE** | Absolute repo path + the **exact files** to touch. Name them. |
| **GROUND RULES** | Include the applicable `CLAUDE.md`, `AGENTS.md`, and source-directory invariants; do not assume every provider loads the same instruction files. |
| **APPROACH / PHASES** | The plan **you** designed — the decomposition, the order, the key decisions. The worker executes your plan; it doesn't re-derive one. For an L/XL part, give it the sub-steps. |
| **ACCEPTANCE / VERIFICATION** | How "done" is proven: which tests to run/add, the real-surface proof (IPC/RPC/HTTP), the gate command. "Done" = these pass. |
| **DO-NOT-TOUCH / BOUNDARIES** | Files owned by other parallel workers; things to leave alone (so concurrent parts don't collide). |
| **CLOSING** | "Commit with a descriptive message when done; end with a concise report of what you did, what you verified, and any failures." (`--pr`: "+ write a PR-description-quality summary.") |

## 0c. Plan, THEN dispatch — the methodology

The orchestrator OWNS the plan. Two shapes, pick per task:

- **Plan-all-then-fan-out** (decomposable undertakings): author the whole **phased plan + every worker's brief FIRST**, then dispatch the batch. You may **use your OWN sub-agents to build the plan** — fan out a `--plan` read-only scout fleet (or your Workflow tool / an `-u` ultracode run) to map the relevant code in parallel, then **synthesize the per-worker briefs** from what they find. Record the plan in the planning home (`docs/neomax-orchestrator/NN-slug/00-plan.md`) so it survives handoff. Then hand each main worker (or group) its complete brief.
- **Plan-one-at-a-time** (sequential / exploratory work): author one worker's full brief, dispatch, integrate the result, then plan the next part from what you learned. Slower but right when each step depends on the last.

Either way, author targeted, complete briefs before a worker starts. Use the current harness's native subagents or read-only Neomax scouts to research when helpful. Do not forward the operator's request verbatim or replace planning with a one-sentence summary.

## 1. Size the task

- **Easy** (quick lookup, single small edit): no neomax — do it yourself or one in-session Agent subagent.
- **Standard** (bugfix, focused feature, tests, docs): `neomax delegate auto "<prompt>"`.
- **Big undertaking — then DECIDE single vs fan-out:**
  - *Deep + cohesive + one-context* (subtle cross-cutting bug, holistic design, tightly coupled logic) → one strong worker on the best-fit engine. `-u` enables the supported Claude/Codex high-compute path; other engines use their selected model's native capabilities.
  - *Decomposable + parallelizable + throughput-bound* → split into non-overlapping parts and launch them concurrently across the enabled engines and accounts. See "Multi-account fan-out" below.

### Multi-account fan-out (the great-orchestrator mode)
1. Partition into independent parts (file/module/feature/repo/concern) — zero overlap.
2. Assign each part by fit, selected model, current load, and provider quota evidence with `--engine claude|codex|opencode|kimi|grok`. Run `neomax find <area>` first to reuse existing context.
3. Launch ALL parts concurrently — separate Bash calls, each into its own worktree (or a `neomax-worktrees` set for cross-repo). `neomax delegate auto` **DETACHES by default**: the worker supervises in its own session and survives your turn-end / any Bash-tool timeout, so it returns immediately with the run id. `TaskCreate` one task per part with its run id. Use `--wait` only when a single dispatch should block in the foreground.
4. The durable ledger + completion inbox + `neomax audit` track every part + sub-agent — nothing is lost across the concurrent sessions. Don't end a turn while parts run without saying what's in flight.
5. Integrate as each finishes (review diff, run tests, merge); `neomax reconcile` until every part is resolved + `ack`/`clean`. One PR per repo (cross-linked) or a combined integration-branch PR.

## 2. Choose the integration model — how the work comes back together

Every worker commits to its own `neomax/<id>` branch in an isolated worktree, so concurrent workers can never overwrite each other. How you reconcile depends on the shape:

**(a) Single worker, or fully independent workers (e.g. one per separate repo).** Default local flow: when each finishes, review its diff (`git diff <base>..<branch>`), run tests, merge its branch into your working branch, `neomax clean <id>`. Add `--pr` to push its branch and open a draft GitHub PR for CI and operator review:
```
neomax delegate --pr auto "<self-contained prompt>"
```

**(b) Several workers contributing to ONE repo/feature → pre-integrate into ONE PR.** Stack every worker on a shared integration branch, then merge them in deliberately, resolve conflicts, and open a single coherent PR:
```
# create the integration branch off the default branch (inside the repo)
git fetch origin && git checkout -b neomax/integration-<job> origin/main && git push -u origin neomax/integration-<job>
# launch workers stacked on it (parallel, non-overlapping pieces)
neomax delegate --base neomax/integration-<job> auto "<piece 1>"   # run_in_background
neomax delegate --base neomax/integration-<job> auto "<piece 2>"   # run_in_background
...
# as each finishes, merge its branch INTO the integration branch, ONE AT A TIME:
git checkout neomax/integration-<job>
git merge --no-ff <worker-branch>      # resolve any conflict yourself, then commit
# after all merged: run the FULL test suite on the integrated result
# then open ONE PR from the integration branch:
neomax pr --branch neomax/integration-<job> --base main --title "<job summary>"
```
Conflict resolution and test-fixing on the integrated branch are the orchestrator's job; that is the point of pre-integrating rather than dumping unrelated PRs on the operator.

## 3. Launching workers

- Partition into NON-OVERLAPPING pieces. Workers branch from HEAD (or `--base`) — commit/stash your own uncommitted changes first if workers need them.
- Each prompt fully self-contained (absolute repo path, acceptance criteria) and ALWAYS ending with: "commit your changes with a descriptive message when done" and "end with a concise report of what you did, what you verified, and any failures." For `--pr` workers, also: "write a clear final summary suitable as a PR description."
- Include all applicable project and source-directory rules in every worker brief: zero feature degradation, no silent fallbacks, original and new tests passing, and proof over the real product surface when required.
- Launch each via Bash with `run_in_background: true` from the relevant directory; independent workers in parallel. Create a TaskCreate entry per worker (note its neomax run id once printed) so progress survives compaction.
- JS/TS repos: fresh worktrees lack node_modules — tell the worker to install, or add a `.neomax-setup.sh` in the repo root (runs automatically in each new worktree).

### Delegating goals & loops

You can hand a worker a completion CONDITION so it keeps working + self-verifying until the condition holds — but the mechanism differs by engine, and an open-ended "loop forever" is NEVER delegated to a headless worker (it would burn quota with no exit).

- **Goal (work until a condition):** `neomax delegate --goal "<condition>" [--max-turns N] auto "<task>"`. The same Neomax flag works across all five engines:
  - **Claude** workers: the real `/goal` built-in — `claude -p` parses `/goal <condition>` from the prompt and the worker iterates across turns until the condition holds, bounded by `--max-turns N`. (Verified live.)
  - **Codex, OpenCode, Kimi, and Grok** workers receive the condition as a plain objective block because their headless modes do not share Claude's `/goal` implementation. Neomax passes a native turn cap where supported and an advisory cap otherwise.
  - Use a goal whenever "done" is a verifiable end-state (tests green, build passes, lint clean) rather than a fixed edit list — the worker won't stop at a plausible-but-wrong point.

- **Loop (repeat / cadence):** delegated repetition is orchestrator-driven. Reinvoke Neomax by count or cadence; for a verifiable finish condition, prefer one `--goal "X"` worker. If the current interactive harness has a native scheduler, use it to run `neomax ls` and `neomax reconcile`; otherwise repeat manually or with an external scheduler.

## 4. Cross-provider workflow fan-out

Interactive `/neomax`, `/rotate`, `/find-issues`, and `/fix-issues` workflows are installed natively for Claude, Codex, OpenCode, Kimi, and Grok. A headless worker does not necessarily parse interactive slash commands, so describe the actual workflow in its worker brief. Independent repositories use one worker per repository and one PR per repository:
```
cd /workspace/project/service-a && neomax delegate -u --pr auto "/security-review"
cd /workspace/project/service-b && neomax delegate -u --pr auto "/security-review"
cd /workspace/project/web       && neomax delegate -u --pr auto "/security-review"
```
They land on three different accounts automatically. When all report, consolidate findings into one summary, calling out issues that span repos (shared API contracts, auth flows).

## 5. Watch + finish — you are the watchdog's watchdog

- Background tasks notify you on completion; the engine guarantees no worker hangs silently. Never end a turn while workers run without reporting what's in flight.
- Read each worker's stderr markers: `NEOMAX STATUS done|limit|error|aborted|stalled|timeout`, `NEOMAX SESSION id=...`, `NEOMAX RESULT branch=... worktree=...`, `NEOMAX PR url=...`.
  - `stalled|timeout|aborted` → `neomax resume <id>` (full context kept); inspect first with `neomax log <id>`.
  - `error` → `neomax log <id>`, fix prompt/env, `neomax retry <id>`.
  - `limit` → engine already failed over / cooled the account; if surfaced, `neomax retry <id>` later.
- Verify before integrating: review the diff yourself, run the relevant tests. Workers can overstate success — never report "done" on a worker's word alone.
- Local merges: one branch at a time, resolve conflicts, then `neomax clean <id>`. PRs: confirm each `NEOMAX PR url=` and report the list to the operator for review. `neomax clean` only AFTER a PR is merged or abandoned.
- Final report: which accounts ran what at which tier, branches merged / PRs opened (with URLs), test results, and anything unverifiable.

## 6. Keep YOURSELF alive — orchestrator self-rotation

The orchestrator consumes quota too. At natural checkpoints run `neomax handoff --check`. When available provider evidence reaches the configured rotation threshold, checkpoint the current state and run `neomax handoff`; it starts a fresh same-engine orchestrator on the best eligible account. OpenCode, Kimi, and Grok rotate reactively when their CLIs expose rate-limit/reset evidence rather than fabricated percentage windows. Stop launching new work after handoff so two orchestrators do not collide. Detached workers continue, and the next orchestrator adopts them with `neomax ls`.
