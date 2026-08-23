"""neomax — ironclad headless coding-agent workers across multiple accounts.

Five engines, one orchestration: Claude (`claude -p`, default), Codex
(`codex exec`, --engine codex), OpenCode (`opencode run`, --engine opencode),
Kimi Code (`kimi -p`, --engine kimi), and Grok Build (`grok -p`, --engine grok).
Any engine can orchestrate; all five account pools share worktree isolation, the durable
~/.neomax registry, resume/retry/kill, usage-window-aware selection + cooldown
failover, sub-agent tracking, and the PR flow are all engine-agnostic.

MODEL DEFAULTS: Claude = Fable 5 (`claude-fable-5[1m]`); Codex = `gpt-5.6-sol`;
OpenCode = Ox Alpha Free (`opencode-go/ox-alpha-free`); Kimi = K3
(`kimi-code/k3`); Grok Build = `grok-4.6`. Use `--model` with any engine, or the
matching `--claude-model` / `--codex-model` / `--opencode-model` /
`--kimi-model` / `--grok-model`, to pass another model supported by that CLI.

Backend for the universal `neomax` command and the provider-pinned cmax/cdxmax,
ocmax/ocx, kmax/kmx, and gmax/gmx launchers.

Usage:
  neomax delegate [-u|--opus] [--engine claude|codex|opencode|kimi|grok] [--model MODEL] [-e EFFORT] [-t MIN] [-s MIN] [-n] [--pr] [--base REF] [--no-worktree] [--wait] auto|N "prompt..."
       dispatch a worker. DETACHES by default — the supervisor runs in its own session so the
       worker survives the launching shell (a Bash call reaped at turn-end or on a tool timeout);
       returns immediately with the run id, track via `ls`/`status`. --wait blocks in the foreground.
  neomax ls [--hook]            list runs (--hook: only unfinished, for SessionStart injection)
  neomax log RUNID              print a run's event log (human summary)
  neomax resume RUNID [PROMPT]  resume a run's session (same account+workdir, context kept)
  neomax retry RUNID [auto|N]   fresh attempt in the same worktree, default: another account
  neomax kill RUNID             kill an orphaned/runaway worker process
  neomax pr RUNID               push a run's branch and open a draft GitHub PR (gh)
  neomax pr --branch NAME [--base REF] [--title T]   PR an arbitrary branch (run inside the repo)
  neomax run-all PLAN.json      fan-out SCHEDULER: run a decomposed plan across many accounts/engines
                                   (leveled by depends_on + area-locks; rolling cap+drain — default ~50
                                   concurrent = lanes-per-account × fleet, REUSES accounts; integrate onto one branch)
  neomax shepherd --branch BR [--base BASE] [--expect SHA] [--merge]   readiness gate → (gated) merge
  neomax issue <open|list|show|next|claim|set|link|comment|close|reconcile> ...   CROSS-REPO issue ledger (the /find-issues + /fix-issues spine): one canonical issue mirrored+synced across ALL project repos; `next` claims the next open one for the fix loop
  neomax ci-sync [--project P] [--apply] [--force]   install the standard neomax-ci GitHub Actions workflow into every repo of a project (uniform cross-repo CI)
  neomax queue <status|reserve|poll|release|set-budget> ...   GLOBAL agent-budget governor (FIFO-by-batch admission): cap concurrent agents (default 50, tunable) + tasks; a task reserves its agent weight, gets freed slots front-of-line first
  neomax find KEYWORD           agent affinity: prior runs that touched a path/keyword (route related work there)
  neomax history [--json] [N]   PERMANENT run history (every run ever, survives clean); `history <id> [--log]` for detail/log
  neomax status [--json]        per-account auth/live/usage-window/cooldown + LIVE sub-agents for all engines (+ run ledger)
  neomax pause|unpause <N|orch|all> [--engine claude|codex|opencode|kimi|grok]   hold an account OUT of (or back INTO) auto-dispatch — live, no restart; keeps it logged in
  neomax paused [--json]        list paused accounts
  neomax orchestrators [--json] [--all]   other concurrent Neomax sessions: who's live, on what project/branch (coordinate so you don't overwrite each other)
  neomax premerge-check [REPO] [--base main]   before merging: git-fetch + did main move (another orchestrator pushed)? + other live orchestrators on this repo
  neomax solo-rotate [--threshold N]   SOLO MODE (`cmax solo`): if the solo session's account >=99% 5h or >=99% weekly, rotate its auth in place to the freshest account (no /login); the per-turn Stop hook + the usage-watcher's rotation_tick do this automatically
  neomax rotate [<acct>...] [--engine E] [--dry-run]   UNIVERSAL /rotate: detect the running provider; Claude swaps auth in place, while Codex/OpenCode/Kimi/Grok cleanly hand off to another isolated same-provider profile. Pure CLI, so it remains callable when the model is blocked
  neomax session-rotate [<acct>...] [--arm] [--threshold N]   low-level Claude-only in-place rotation primitive used by the universal command
  neomax rotate-tick [--active]   run ONE model-free rotation sweep now (rotate any solo/armed/orchestrator session over 5h/weekly threshold) — the usage watcher runs this every ~30s automatically
  neomax handoff --check        is THIS orchestrator account near its 5h/weekly limit? (exit 10 = rotate advised)
  neomax handoff [--to ACCOUNT]   ROTATE the orchestrator to a same-engine account (clean handoff, nothing lost)
  neomax modes [--json]         the orchestration MODES + how to start each + account/login commands (cheat-sheet)
  neomax usage [--json] [--days N | --since 37m|2h | --all]  WHOLE-FLEET token usage + est. cost (orchestrator + workers + sub-agents) per account/model/provider; --since sums a precise window (e.g. a finished goal/session)
  neomax usage-watch [--once]   background watcher: capture every completion's tokens live (runs via launchd, auto)
  neomax audit [RUNID]          print the durable append-only event timeline (whole ledger or one run)
  neomax reconcile [--heal]     sweep the ledger; --heal auto-resumes/retries/kills stale runs (bounded, deduped)
  neomax ack RUNID|--all        mark finished run(s) acknowledged (orchestrator received the result)
  neomax clean [--force] RUNID|--done   remove worktree+branch+logs+registry
                                   (refuses runs with unmerged work unless --force; --done: all finished)

Options:
  --engine E    claude (default) | codex | opencode | kimi | grok
  --model M     Model for the selected engine. The engine CLI validates availability.
                OpenCode model IDs must use the local registry's provider/model form.
  --claude-model M / --codex-model M / --opencode-model M / --kimi-model M /
  --grok-model M    Engine-specific forms; useful in scripts and existing plans.
  -u            ultracode worker (Claude): xhigh + workflows. On codex → xhigh reasoning.
  --opus        Explicitly select Claude Opus 5. Fable 5 remains the default unless this
                flag or an equivalent --model/--claude-model value is supplied.
  --codex-model M   Keeps sol/terra/luna aliases; any explicit Codex model is accepted.
  --kimi-model M    Keeps k3/k2.7 aliases; any explicit Kimi model is accepted.
  --goal COND   delegate a GOAL: the worker works + self-verifies until COND holds.
                Claude uses the real `/goal` built-in (parsed from the prompt);
                Codex/OpenCode/Kimi/Grok headless runs get COND as a plain objective block.
                Pair with --max-turns N to bound it (Claude; advisory on the others).
  --max-turns N cap a delegated goal's turns (Claude/Grok `--max-turns`; Codex/OpenCode/Kimi:
                advisory, folded into the objective block for those engines).
  -e EFFORT     explicit effort. claude: low|medium|high|xhigh|max. codex: low|medium|high|xhigh
  -t MIN        wall-clock timeout in minutes (default 240; 0 disables)
  -s MIN        stall timeout in minutes — no stream events for this long kills the
                worker as stalled (default: 30, or 60 with -u; 0 disables)
  -n            no automatic account failover on usage-limit/error
  --brief       acknowledge an intentionally SHORT worker prompt (trivial task) and
                suppress the thin-delegation warning. Default: neomax WARNS when a
                delegated prompt looks under-specified — author a full planned brief.
  --plan        PLAN-MODE scout: read-only worker (Claude/Codex sandbox; OpenCode plan
                agent with writes denied) in the real repo (implies --no-worktree) — fan out
                read-only scouts across repos/areas for fast broad-scope planning,
                collect their reports, write the master plan, then dispatch normal
                (writing) workers. Plan-mode in, plan-mode out: nothing is modified.
  --pr          on success-with-changes, push the branch and open a draft GitHub PR
  --base REF    branch the worker's worktree from REF (e.g. an integration branch)
                instead of HEAD — for stacking many workers into one combined PR

Statuses: running, done, limit, error, aborted (clean interrupt, resumable),
stalled/timeout (watchdog kill, resumable), interrupted (neomax itself died).

Limit detection is event-based (rate_limit_event status=rejected, assistant
error=rate_limit, result api_error_status=429) with resetsAt captured into a
per-account cooldown ledger that `auto` selection respects. Session IDs are
pre-generated (--session-id) so every run is resumable even if it dies at spawn.
Resume runs from the run's original workdir (transcripts are cwd-bound) and never
overwrites the canonical session id (init events lie on resume — issue #58760).

State lives in ~/.neomax (registry + event logs + cooldown) — survives any session.
Worker stdout (final result text) is this command's stdout; NEOMAX STATUS/SESSION/
RESULT lines and hints are stderr.

Test hooks: NEOMAX_CLAUDE_BIN, NEOMAX_CODEX_BIN, NEOMAX_OPENCODE_BIN,
NEOMAX_KIMI_BIN, NEOMAX_GROK_BIN, NEOMAX_PROFILES /
NEOMAX_CODEX_PROFILES / NEOMAX_OPENCODE_PROFILES /
NEOMAX_KIMI_PROFILES / NEOMAX_GROK_PROFILES.
"""


def require_orchestrator(verb):
    """neomax DELEGATION (spawning worker sessions on your OTHER accounts) is ORCHESTRATOR-ONLY —
    only sessions launched via `neomax` or a provider-pinned `*max` launcher, which set
    NEOMAX_ROLE. A plain provider CLI session or
    `cmax solo` must NOT delegate; it does the work in-session with the built-in Workflow + Agent
    (sub-agent) tools (same account). Exempt: a delegated worker re-dispatching (NEOMAX_WORKER),
    or an explicit NEOMAX_ALLOW_DELEGATE=1 override. Read-only verbs (status/ls/usage/...) are NOT
    gated — only the dispatch verbs (`auto`/`<N>`/`run-all`)."""
    from . import config as _config
    from . import models as _models

    if (
        _config.os.environ.get("NEOMAX_ROLE")
        or _config.os.environ.get("NEOMAX_WORKER")
        or _config.os.environ.get("NEOMAX_ALLOW_DELEGATE") == "1"
    ):
        return
    mode = (
        "a `cmax solo`"
        if _config.os.environ.get("NEOMAX_MODE") == "solo"
        else "a plain provider CLI"
    )
    _models.err(
        "neomax %s: REFUSED — this is %s session, NOT an orchestrator (NEOMAX_ROLE is unset).\nneomax DELEGATION (spawning worker sessions on your OTHER accounts) is ONLY for\norchestrator sessions launched via `neomax`, `cmax`, `cdxmax`, `ocmax`, `kmax`, or `gmax`. In THIS session, do the work\nYOURSELF: use the Workflow tool + the Agent (sub-agent) tool — they run sub-agents\nIN-PROCESS on this account (that's exactly what 'solo + workflows + sub-agents' means).\nTo genuinely delegate across accounts, relaunch via `neomax` or the required provider's `*max` launcher. (Override: NEOMAX_ALLOW_DELEGATE=1.)"
        % (verb, mode)
    )
    _config.sys.exit(2)


def main():
    from . import account_controls as _account_controls
    from . import auth_rotation as _auth_rotation
    from . import cleanup as _cleanup
    from . import config as _config
    from . import diffs as _diffs
    from . import dispatch as _dispatch
    from . import handoff as _handoff
    from . import history as _history
    from . import interactive_sessions as _interactive_sessions
    from . import issue_commands as _issue_commands
    from . import modes as _modes
    from . import orchestrator_selection as _orchestrator_selection
    from . import orientation as _orientation
    from . import premerge as _premerge
    from . import projects as _projects
    from . import queue as _queue
    from . import reconcile as _reconcile
    from . import rotation_commands as _rotation_commands
    from . import rotation_watch as _rotation_watch
    from . import run_lifecycle as _run_lifecycle
    from . import scheduler as _scheduler
    from . import shepherd as _shepherd
    from . import solo as _solo
    from . import subagent_history as _subagent_history
    from . import tasks as _tasks
    from . import usage_reports as _usage_reports
    from . import usage_search as _usage_search

    argv = _config.sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help", "commands"):
        print(__doc__)
        _config.sys.exit(0)
    cmd = argv[0]
    if cmd == "__supervise":
        _run_lifecycle.cmd_supervise(argv[1:])
        return
    if cmd == "ls":
        _cleanup.cmd_ls(argv[1:])
    elif cmd == "log":
        _cleanup.cmd_log(argv[1:])
    elif cmd == "resume":
        _dispatch.cmd_resume(argv[1:])
    elif cmd == "retry":
        _dispatch.cmd_retry(argv[1:])
    elif cmd == "kill":
        _run_lifecycle.cmd_kill(argv[1:])
    elif cmd == "pr":
        _run_lifecycle.cmd_pr(argv[1:])
    elif cmd == "reconcile":
        _reconcile.cmd_reconcile(argv[1:])
    elif cmd == "ack":
        _usage_search.cmd_ack(argv[1:])
    elif cmd == "audit":
        _usage_search.cmd_audit(argv[1:])
    elif cmd == "find":
        _usage_search.cmd_find(argv[1:])
    elif cmd == "history":
        _history.cmd_history(argv[1:])
    elif cmd == "status":
        _usage_reports.cmd_status(argv[1:])
    elif cmd == "pause":
        _account_controls.cmd_pause(argv[1:], paused=True)
    elif cmd == "unpause":
        _account_controls.cmd_pause(argv[1:], paused=False)
    elif cmd == "paused":
        _account_controls.cmd_paused(argv[1:])
    elif cmd in ("orchestrators", "orch-list", "orchs"):
        _account_controls.cmd_orchestrators(argv[1:])
    elif cmd in ("orch-register", "orch_register"):
        _account_controls.cmd_orch_register(argv[1:])
    elif cmd in ("orch-unregister", "orch_unregister"):
        _account_controls.cmd_orch_unregister(argv[1:])
    elif cmd in ("premerge-check", "premerge"):
        _premerge.cmd_premerge_check(argv[1:])
    elif cmd in ("pick-orch", "pick_orch"):
        _orchestrator_selection.cmd_pick_orch(argv[1:])
    elif cmd in ("pick-neomax", "pick_neomax"):
        _orchestrator_selection.cmd_pick_neomax(argv[1:])
    elif cmd in ("orch-on", "orch_on"):
        _orchestrator_selection.cmd_orch_on(argv[1:])
    elif cmd in ("solo-rotate", "solo_rotate"):
        _solo.cmd_solo_rotate(argv[1:])
    elif cmd in ("solo-setup", "solo_setup"):
        _solo.cmd_solo_setup(argv[1:])
    elif cmd in ("session-rotate", "session_rotate"):
        _rotation_commands.cmd_session_rotate(argv[1:])
    elif cmd == "rotate":
        _rotation_commands.cmd_rotate(argv[1:])
    elif cmd in ("rotate-tick", "rotate_tick"):
        _rotation_watch.cmd_rotate_tick(argv[1:])
    elif cmd == "handoff":
        _handoff.cmd_handoff(argv[1:])
    elif cmd == "modes":
        _modes.cmd_modes(argv[1:])
    elif cmd == "sessions":
        _interactive_sessions.cmd_sessions(argv[1:])
    elif cmd == "subagents":
        _subagent_history.cmd_subagents(argv[1:])
    elif cmd == "diff":
        _diffs.cmd_diff(argv[1:])
    elif cmd in ("subagent-diff", "subagent_diff"):
        _subagent_history.cmd_subagent_diff(argv[1:])
    elif cmd == "projects":
        print(_config.json.dumps(_projects.load_projects(), indent=2))
    elif cmd in ("project-register", "project_register", "register-project"):
        _projects.cmd_project_register(argv[1:])
    elif cmd in ("project-unregister", "project_unregister", "unregister-project"):
        _projects.cmd_project_register(["--unregister"] + argv[1:])
    elif cmd in ("task", "tasks", "backlog"):
        _tasks.cmd_task(argv[1:])
    elif cmd in ("rotate-auth", "rotate_auth"):
        _auth_rotation.cmd_rotate_auth(argv[1:])
    elif cmd == "orient":
        _orientation.cmd_orient(argv[1:])
    elif cmd == "usage":
        _usage_reports.cmd_usage(argv[1:])
    elif cmd in ("usage-watch", "usage_watch"):
        _rotation_watch.cmd_usage_watch(argv[1:])
    elif cmd in ("keepalive", "keep-alive"):
        _rotation_watch.cmd_keepalive(argv[1:])
    elif cmd in ("turn-hook", "turn_hook"):
        _rotation_watch.cmd_turn_hook(argv[1:])
    elif cmd in ("model-guard", "model_guard"):
        _rotation_watch.cmd_model_guard(argv[1:])
    elif cmd in ("usage-hook", "usage_hook"):
        _rotation_watch.cmd_usage_hook(argv[1:])
    elif cmd in ("run-all", "runall"):
        require_orchestrator("run-all")
        _scheduler.cmd_run_all(argv[1:])
    elif cmd == "shepherd":
        require_orchestrator("shepherd")
        _shepherd.cmd_shepherd(argv[1:])
    elif cmd == "issue":
        _issue_commands.cmd_issue(argv[1:])
    elif cmd == "ci-sync":
        _issue_commands.cmd_ci_sync(argv[1:])
    elif cmd == "queue":
        _queue.cmd_queue(argv[1:])
    elif cmd == "clean":
        _cleanup.cmd_clean(argv[1:])
    elif cmd == "tidy":
        _cleanup.cmd_tidy(argv[1:])
    else:
        if cmd == "auto" or cmd.isdigit():
            require_orchestrator("auto" if cmd == "auto" else "dispatch")
        _dispatch.cmd_run(argv)
