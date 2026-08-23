"""Provider-neutral orchestrator orientation."""


def orient_directive():
    """The fleet-orchestrator session opener, with engine/mode + fleet computed LIVE from
    the launcher env + discovered profiles (NO hardcoded account count or mode). One source
    of truth for both the SessionStart auto-inject and a manual `neomax orient`. Solo mode is
    deliberately NOT handled here — `cmax solo` is a plain session and injects nothing.

    SHAPE: this is a TOOLBOX,
    not a playbook. It states the session's FACTS (engine, scope, fleet, project/paths), lists the
    CAPABILITIES the harness gives the orchestrator (delegation, run-all, recovery, account
    control, auth rotation, Codex, backlog/issues, usage) so it knows what it can drive, plus the
    safety non-negotiables (model policy, git safety). Exactly ONE operating principle is stated:
    use sub-agents/workers, and run parallel work concurrently spread EVENLY across accounts.
    NEVER put worker-count targets, caps, "max N sub-agents", fan-out mandates, or engine-routing
    rules in here — no numbers. Everything else about HOW to work lives in the project's
    CLAUDE.md / AGENTS.md (+ its registered ORCHESTRATOR_OPENER.md supplement)."""
    from . import config as _config
    from . import handoff as _handoff
    from . import models as _models
    from . import modes as _modes
    from . import projects as _projects
    from . import usage_windows as _usage_windows

    _cwd = _config.os.getcwd()
    launch_root = _config.os.environ.get("NEOMAX_PROJECT_ROOT")
    if _config.os.environ.get("NEOMAX_ROLE") and launch_root:
        _projects.ensure_launch_project(launch_root)
    role = _handoff.orchestrator_engine()
    scope = _config.allowed_engines()
    names = {
        "claude": "Claude",
        "codex": "Codex",
        "opencode": "OpenCode",
        "kimi": "Kimi",
        "grok": "Grok",
    }
    orch = "%s %s" % (names[role], _models.default_model(role))
    workers = " + ".join(
        (
            names[e]
            for e in ("claude", "codex", "opencode", "kimi", "grok")
            if e in scope
        )
    )
    workers += " only" if len(scope) == 1 else ""
    bits = []
    if "claude" in scope:
        bits.append("%d Claude" % len(_models.engine_profiles("claude")))
    if "codex" in scope:
        bits.append("%d Codex" % len(_models.engine_profiles("codex")))
    if "opencode" in scope:
        bits.append("%d OpenCode" % len(_models.engine_profiles("opencode")))
    if "kimi" in scope:
        bits.append("%d Kimi" % len(_models.engine_profiles("kimi")))
    if "grok" in scope:
        bits.append("%d Grok" % len(_models.engine_profiles("grok")))
    fleet = " + ".join(bits) or "no in-scope"
    proj = _projects.project_of(_config.os.getcwd())
    pcfg = _projects.load_projects().get(proj) or {} if proj else {}
    if not proj:
        lines = []
        for nm, cfg in _projects.load_projects().items():
            lines.append(
                "    - %s : %s (branch prefix `%s/`)"
                % (nm, cfg.get("root", "?"), cfg.get("branch_prefix", nm[:3]))
            )
        proj_line = (
            "You are the ORCHESTRATOR in **MULTI-PROJECT mode** — this session can drive ANY of the registered projects (you were launched outside a single project's root). The fleet/accounts are shared; each project stays fully segmented by its own repos, branch prefix, and `project=` tag.\n"
            + "\n".join(lines)
            + "\n  TO TARGET A PROJECT: `cd` into its root (or a repo under it) BEFORE each `neomax` dispatch — neomax tags the run + builds the worktree from that cwd, so the work lands in the right project/branch automatically. Use `/project <name>` (or just say which project) to switch focus; read that project's CLAUDE.md + planning home the first time you touch it. NEVER mix two projects' work on one branch. (Prefer one project per session for focused work; switch here only when you want both from one place.)\n"
        )
    else:
        root = pcfg.get("root", "?")
        desc = (
            pcfg.get("desc")
            or "the %s project — root %s, repos: %s"
            % (proj, root, ", ".join(pcfg.get("repos") or []))
        ).replace("{root}", root)
        prefix = pcfg.get("branch_prefix", proj[:3])
        brain = pcfg.get("brain", "CLAUDE.md")
        planning = pcfg.get("planning", "docs/neomax-orchestrator")
        ob = pcfg.get("orch_brain")
        orch_brain_clause = (
            "%s/%s (your orchestrator brain) + " % (root, ob) if ob else ""
        )
        proj_line = (
            "You are the ORCHESTRATOR for the **%s** project (NOT any other project on this machine) — %s. Branch/worktree namespace is `%s/` — segmented from every other project; never touch another project's repos/worktrees/branches. Read %s%s/%s (project rules) + %s/AGENTS.md + any per-subdir AGENTS.md before delegating into a repo. Plan + track in %s/%s (the orchestrator planning home — read its 00-README.md). The dashboard tags every run/session/sub-agent `project=%s` so you can filter it.\n"
            % (
                proj,
                desc,
                prefix,
                orch_brain_clause,
                root,
                brain,
                root,
                root,
                planning,
                proj,
            )
        )
        instruction_files = [
            pcfg.get("brain", "CLAUDE.md"),
            pcfg.get("agents", "AGENTS.md"),
        ]
        if pcfg.get("auto_registered") and (
            not any(
                (
                    _config.os.path.isfile(_config.os.path.join(root, path))
                    for path in instruction_files
                    if path
                )
            )
        ):
            proj_line += _modes._new_project_directive(root, proj, pcfg)
    if not proj:
        step1 = "Run `neomax projects` for the registered projects, then read this machine's global rules (~/.claude/CLAUDE.md). Before touching ANY project, read its root CLAUDE.md + AGENTS.md + its planning home's 00-README.md — you're in multi-project mode, so orient per project as you focus it."
        sot_clause = ""
        planning_home = "PLANNING HOME: each project plans + tracks under its OWN planning home (see `neomax projects`); `cd` into the project first, then read its planning home's 00-README.md and add a numbered package there. Never plan one project's work under another.\n\n"
    else:
        p_root = pcfg.get("root", "?")
        p_brain = pcfg.get("brain", "CLAUDE.md")
        p_agents = pcfg.get("agents", "AGENTS.md")
        p_planning = pcfg.get("planning", "docs/neomax-orchestrator")
        p_ob = pcfg.get("orch_brain")
        ob_clause = (
            "%s/%s (your orchestrator brain) + " % (p_root, p_ob) if p_ob else ""
        )
        harness_brain = pcfg.get("harness_brain")
        harness_clause = ""
        if harness_brain:
            hb_path = (
                harness_brain
                if _config.os.path.isabs(harness_brain)
                else _config.os.path.join(_config.NEOMAX_REPO_DIR, harness_brain)
            )
            harness_clause = "%s (local harness override) + " % hb_path
        step1 = (
            "Read %s%s%s/%s + %s/%s + any nearer per-directory instructions before delegating. Use the project's rules and do not crawl unrelated code."
            % (harness_clause, ob_clause, p_root, p_brain, p_root, p_agents)
        )
        sot_clause = ""
        planning_home = (
            "PLANNING HOME: use %s/%s when that project provides it; otherwise keep plans and work logs in the project's own documented location.\n\n"
            % (p_root, p_planning)
        )
    _directive = (
        "SYSTEM — Neomax orchestrator session (multi-account fleet). Orient (below), then OPEN your reply with a 3-5 line confirmation; if my first message gives no task yet, stop there and wait.\n\n"
        + proj_line
        + "  Orchestrator engine : "
        + orch
        + "\n  Worker pool (scope) : "
        + workers
        + "   (from --workers; neomax HARD-enforces it — an out-of-scope engine is refused)\n  Fleet configured    : "
        + fleet
        + " account(s) — VERIFY which are actually authenticated/available LIVE with `neomax status` (don't assume; some may be logged out or cooled).\n  Worker model defaults: "
        + " · ".join(
            (
                "%s=%s" % (names[e], _models.default_model(e))
                for e in ("claude", "codex", "opencode", "kimi", "grok")
                if e in scope
            )
        )
        + "\n"
        + (
            "  DEDICATED ORCHESTRATOR MODE: this session runs on the RESERVED orchestrator account — it is excluded from the worker pool (neomax enforces this; never dispatch work to it). If it nears its limits, use `neomax handoff` to move to another same-engine account.\n"
            if _models.orch_reserved()
            else ""
        )
        + "\nYOUR TOOLBOX — this harness gives you a whole fleet plus your own in-session agents. `neomax help` = the full command registry (every flag); `neomax modes` = the launch + account/login cheat-sheet. What you can drive:\n  • Your own sub-agents  the Agent tool + Workflow, inside THIS session, on "
        + orch
        + '.\n  • Delegate a task      `neomax delegate [--engine claude|codex|opencode|kimi|grok] [--model MODEL] [-e EFFORT] [--goal COND] [--pr] [--plan] auto|N "brief"` → a worktree-isolated headless worker on another account; detaches by default, resumable, tracked. `--plan` = read-only scout.\n  • Fan out a plan       `neomax run-all PLAN.json` — the scheduler: dependency-leveled, area-locked so same-file parts serialize, conflict-self-healing, integrates onto one branch.\n  • Watch / recover      `neomax ls` · `neomax status` · `neomax log RUNID` · `neomax audit [RUNID]` · `neomax history` · `neomax find KEYWORD` · `neomax reconcile [--heal]` · `neomax ack RUNID|--all` · `neomax clean RUNID|--done` — nothing finished is ever silently missed.\n  • Steer a run          `neomax resume RUNID` · `neomax retry RUNID` · `neomax kill RUNID` (= lossless pause) · `neomax pr RUNID` · `neomax shepherd ...`.\n  • Accounts             `neomax status` · `neomax pause`/`neomax unpause` · `neomax paused` · `neomax orchestrators` (other live sessions) · `neomax premerge-check` · `cmax N` / `cdx login N` / `ocx login N` / `kmx login N` / `gmx login N` to authenticate one.\n  • Rotate/handoff       `/rotate [<acct>]` and `neomax rotate [<acct>]` detect this orchestrator\'s provider. Claude swaps in place; Codex/OpenCode/Kimi/Grok launch a clean same-provider handoff without copying credentials. The CLI path remains available when a model request is rate-limited; `neomax handoff` moves the orchestrator and `neomax rotate-auth` remains the advanced Claude/Codex credential-swap primitive.\n  • Models               defaults stay pinned; `--model MODEL` selects another model supported by that engine. Every launcher also accepts `--claude-model`, `--codex-model`, `--opencode-model`, `--kimi-model`, and `--grok-model` for its worker pools.\n  • OpenCode registry    `ocx models [N] [provider]`; use its qualified `provider/model` IDs.\n  • Backlog + issues     `neomax task add|start|done|drop|list` (durable — trust it over chat memory) · `neomax issue open|list|next|claim|close` (cross-repo issue ledger) · `neomax ci-sync`.\n  • Cost + dashboard     `neomax usage [--since ...]` · `neomax portal`.\n\nLAUNCH MODES (which engine orchestrates + which accounts it may use). The scope above was set by the command that started this session and is FIXED for its lifetime — neomax hard-enforces it:\n'
        + _modes.orch_modes_lines()
        + '\n  Also: `cmax N` / `cdx login N` / `ocx login N` / `kmx login N` / `gmx login N` (authenticate one account) · `cmax orchestrator` / `cdx orch` / `ocx orch` / `kmx orch` / `gmx orch` (set up the reserved orch account) · `neomax status` · `neomax portal` · `cmax resume [<id>]` / `cdxmax resume [<id>]` / `ocmax resume [<id>]` / `kmax resume [<id>]` / `gmax resume [<id>]`.\n  IF I ASK YOU MID-SESSION TO USE ONLY ONE ENGINE ("only use codex", "stay off Claude"): you CANNOT change this session\'s scope — but you can honor it two ways, both live: pass `--engine codex` on every dispatch, and/or `neomax pause all --engine claude` (hard-excludes those accounts from auto-dispatch, no restart, still logged in; `unpause` restores them). To WIDEN scope beyond what this session launched with, tell me to relaunch in the matching mode above.\n\nHOW TO USE IT: lean on sub-agents and workers. Anything that can run in parallel SHOULD run in parallel, spread EVENLY across the available accounts rather than stacked on one — that even distribution is the whole point of the fleet, and it is how you finish fastest. No target or limit on how many: split the work along its real seams and run those concurrently. Beyond that, HOW to work — decomposition, engine routing, briefing standards, review gates — comes from the project\'s own CLAUDE.md / AGENTS.md (and any per-directory AGENTS.md), not from this opener.\n\nOrient: '
        + step1
        + " Then `neomax reconcile` + `neomax ls` (in-flight workers + completion inbox), `neomax status` (live auth + 5h/weekly usage), `neomax task list` (the backlog).\n\n"
        + planning_home
        + "ROTATION (automatic): "
        + (
            (
                "OpenCode exposes no account-window telemetry, so OX rotation is reactive: a structured 429 cools that profile using Retry-After/reset headers, then workers fail over. Use `neomax handoff` for a manual orchestrator move"
                if role == "opencode"
                else "Kimi exposes no account-window telemetry, so rotation is reactive: a structured 429 cools that profile using Retry-After/reset headers, then workers fail over. Use `neomax handoff` or `kmax resume` for a manual orchestrator move"
                if role == "kimi"
                else "Grok exposes no account-window telemetry, so rotation is reactive: a structured 429 cools that profile, then workers fail over. Use `neomax handoff` or `gmax resume` for a manual orchestrator move"
            )
            if role in ("opencode", "kimi", "grok")
            else "this session is auto-armed at "
            + (
                ">=99% of the 5-hour window or >=99% of the weekly window"
                if _usage_windows.engine_has_5h(role)
                else ">=99% of the weekly window (Codex has no 5-hour limit)"
            )
            + (
                " (/login adopts the fresh account instantly)"
                if role == "claude"
                else " (quit + `cdxmax resume` adopts it instantly)"
            )
        )
        + ". Workers that hit a usage limit cool that account until its real reset and FAIL OVER to a fresh account automatically — so a limit never stops the work.\n\nNon-negotiables: model defaults remain explicit (Claude Fable 5, Codex gpt-5.6-sol, OpenCode `opencode-go/ox-alpha-free`, Kimi K3, Grok `grok-4.6`); every local-CLI-supported override is allowed and must remain recorded on the run; git safety (commit often, NEVER force-push / reset --hard / checkout -- . / git clean / stash-drop; PUSH branches + OPEN PRs freely, but NEVER merge to main without operator approval); never end a turn with workers running without saying what is in flight.\n\nThen lead with the 3-5 line confirmation: engine + mode, the LIVE fleet from `neomax status`, "
        + sot_clause
        + "and the project brain(s) you just read."
    )
    if proj and pcfg.get("opener"):
        opath = _config.os.path.join(
            _config.os.path.abspath(_config.os.path.expanduser(pcfg.get("root", ""))),
            pcfg["opener"],
        )
        try:
            with open(opath) as f:
                supp = f.read(8000).strip()
            if supp:
                _directive += "\n\n— PROJECT-SPECIFIC ORIENTATION (%s/%s) —\n%s" % (
                    proj,
                    pcfg["opener"],
                    supp,
                )
        except OSError:
            pass
    return _directive


def cmd_orient(argv):
    """Print the live fleet-orchestrator opener (engine/mode + fleet computed dynamically).
    neomax orient           print it (manual paste / inspection)
    neomax orient --hook    SessionStart auto-inject — emits ONLY in an interactive
                               orchestrator session (NEOMAX_ROLE set, not a neomax worker);
                               silent otherwise so plain `claude` + workers are untouched."""
    from . import config as _config
    from . import handoff as _handoff
    from . import orchestrators as _orchestrators
    from . import rotation_state as _rotation_state
    from . import usage_windows as _usage_windows

    if "--hook" in argv:
        if (
            not _config.os.environ.get("NEOMAX_ROLE")
            or _config.os.environ.get("NEOMAX_WORKER")
            or _config.os.environ.get("NEOMAX_MODE") == "solo"
        ):
            return
        if _handoff.orchestrator_engine() == "claude":
            try:
                _rotation_state.set_armed_rotate(
                    _rotation_state._current_session_profile(),
                    _usage_windows.ORCH_5H_ROTATE,
                    None,
                    weekly_threshold=_usage_windows.ORCH_7D_ROTATE,
                    auto=True,
                )
            except Exception:
                pass
        print(
            _config.json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": _orchestrators.colocation_banner(
                            _handoff.orchestrator_engine()
                        )
                        + orient_directive(),
                    }
                }
            )
        )
        return
    if not _config.os.environ.get("NEOMAX_ROLE"):
        print(
            "neomax orient is available only inside a session launched by neomax or a provider-pinned cmax/cdxmax/ocmax/kmax/gmax launcher (NEOMAX_ROLE is not set). Plain provider sessions must use their normal workspace instructions and native sub-agents.",
            file=_config.sys.stderr,
        )
        raise SystemExit(2)
    print(
        _orchestrators.colocation_banner(_handoff.orchestrator_engine())
        + orient_directive()
    )
