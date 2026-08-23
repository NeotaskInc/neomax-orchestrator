"""Worker dispatch, resume, and retry commands."""


def cmd_run(argv):
    from . import config as _config
    from . import failover as _failover
    from . import gitops as _gitops
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import premerge as _premerge
    from . import projects as _projects
    from . import run_lifecycle as _run_lifecycle
    from . import runs as _runs
    from . import selection as _selection
    from . import usage_windows as _usage_windows

    effort = None
    ultra = False
    opus = False
    generic_model = None
    model_overrides = {e: None for e in _models.ENGINES}
    engine = "claude"
    wall_min = 240.0
    stall_min = None
    no_failover = False
    brief_ok = False
    plan_mode = False
    no_worktree = bool(_config.os.environ.get("NEOMAX_NO_WORKTREE"))
    want_pr = False
    base_ref = None
    fixed_runid = None
    tag = None
    goal = None
    max_turns = None
    engine_set = False
    detach = None
    args = list(argv)
    while args and args[0].startswith("-"):
        a = args.pop(0)
        if a == "-u":
            ultra = True
        elif a == "--opus":
            opus = True
        elif a == "--model":
            generic_model = args.pop(0)
        elif a in ("--codex-model", "-cm"):
            model_overrides["codex"] = args.pop(0)
        elif a in (
            "--claude-model",
            "--opencode-model",
            "--kimi-model",
            "--grok-model",
        ):
            model_overrides[a[2:-6]] = args.pop(0)
        elif a == "--engine":
            engine = args.pop(0)
            engine_set = True
        elif a == "--goal":
            goal = args.pop(0)
        elif a == "--max-turns":
            max_turns = args.pop(0)
        elif a == "--run-id":
            fixed_runid = args.pop(0)
        elif a == "--tag":
            tag = args.pop(0)
        elif a == "-e":
            effort = args.pop(0)
            if effort not in ("low", "medium", "high", "xhigh", "max"):
                _models.err(
                    "neomax: invalid effort %r (low|medium|high|xhigh|max)" % effort
                )
                _config.sys.exit(2)
        elif a == "-t":
            wall_min = float(args.pop(0))
        elif a == "-s":
            stall_min = float(args.pop(0))
        elif a == "-n":
            no_failover = True
        elif a == "--no-worktree":
            no_worktree = True
        elif a == "--brief":
            brief_ok = True
        elif a == "--plan":
            plan_mode = True
            no_worktree = True
        elif a == "--pr":
            want_pr = True
        elif a == "--base":
            base_ref = args.pop(0)
        elif a in ("--wait", "--foreground", "--fg"):
            detach = False
        elif a == "--detach":
            detach = True
        else:
            _premerge.usage()
    if detach is None:
        detach = fixed_runid is None
    if engine not in _models.ENGINES:
        _models.err(
            "neomax: unknown engine %r (use: claude | codex | opencode | kimi | grok)"
            % engine
        )
        _config.sys.exit(2)
    scope = _config.allowed_engines()
    if not engine_set and engine not in scope:
        engine = next(
            (e for e in ("opencode", "grok", "kimi", "codex", "claude") if e in scope),
            engine,
        )
    if engine not in scope:
        _models.err(
            "neomax: engine %r is out of this session's fleet scope (NEOMAX_FLEET=%s). Allowed: %s."
            % (
                engine,
                _config.os.environ.get("NEOMAX_FLEET", "all"),
                ", ".join(sorted(scope)),
            )
        )
        _config.sys.exit(2)
    if goal is not None:
        goal = goal.strip()
        if not goal:
            _models.err("neomax: --goal requires a non-empty condition")
            _config.sys.exit(2)
        if len(goal) > 4000:
            _models.err(
                "neomax: --goal too long (max 4000 chars; point it at a file instead)"
            )
            _config.sys.exit(2)
    if max_turns is not None:
        try:
            max_turns = int(max_turns)
            if max_turns < 1:
                raise ValueError
        except ValueError:
            _models.err("neomax: --max-turns must be a positive integer")
            _config.sys.exit(2)
        if not goal:
            _models.err("neomax: --max-turns requires --goal")
            _config.sys.exit(2)
    foreign = [e for (e, value) in model_overrides.items() if value and e != engine]
    if foreign:
        _models.err(
            "neomax: --%s-model requires --engine %s" % (foreign[0], foreign[0])
        )
        _config.sys.exit(2)
    specific_model = model_overrides[engine]
    if generic_model and specific_model and (generic_model != specific_model):
        _models.err("neomax: --model and --%s-model disagree" % engine)
        _config.sys.exit(2)
    chosen_model = generic_model or specific_model
    if opus and engine != "claude":
        _models.err("neomax: --opus is only valid with --engine claude")
        _config.sys.exit(2)
    if opus:
        if (
            chosen_model
            and _models.resolve_claude_model(chosen_model).split("[")[0]
            != _config.CLAUDE_OPUS_MODEL
        ):
            _models.err(
                "neomax: --opus conflicts with the explicit Claude model %r"
                % chosen_model
            )
            _config.sys.exit(2)
        chosen_model = chosen_model or _config.CLAUDE_OPUS_MODEL_1M
    if engine == "opencode":
        if opus or ultra or effort:
            _models.err("neomax: --opus/-u/-e do not apply to OpenCode workers")
            _config.sys.exit(2)
        model = _models.resolve_opencode_model(chosen_model)
        effort = None
        ultra = False
    elif engine == "kimi":
        if opus or ultra or effort:
            _models.err("neomax: --opus/-u/-e do not apply to Kimi workers")
            _config.sys.exit(2)
        model = _models.resolve_kimi_model(chosen_model)
        effort = None
        ultra = False
    elif engine == "grok":
        if opus or ultra or effort:
            _models.err("neomax: --opus/-u/-e do not apply to Grok workers")
            _config.sys.exit(2)
        model = _models.resolve_grok_model(chosen_model)
        effort = None
        ultra = False
    elif engine == "codex":
        model = _models.resolve_codex_model(chosen_model)
        if effort and effort not in _models.CODEX_EFFORTS:
            _models.err(
                "neomax: codex effort must be one of %s"
                % ", ".join(_models.CODEX_EFFORTS)
            )
            _config.sys.exit(2)
        effort = effort or ("xhigh" if ultra else "high")
        ultra = False
    else:
        model = _models.resolve_claude_model(chosen_model)
        if ultra and (not effort):
            effort = "xhigh"
    if stall_min is None:
        stall_min = 60.0 if ultra else 30.0
    if len(args) < 2 or not (args[0] == "auto" or args[0].isdigit()):
        _premerge.usage()
    selector = args.pop(0)
    prompt = " ".join(args)
    _run_lifecycle.delegation_brief_check(prompt, brief_ok=brief_ok, ultra=ultra)
    runid = fixed_runid or _config.time.strftime("%Y%m%d-%H%M%S") + "-" + str(
        _config.os.getpid()
    )
    if not fixed_runid:
        live_now = _usage_windows.fleet_live_workers()
        if live_now >= _usage_windows.FLEET_CONCURRENCY_CAP:
            _models.err(
                "neomax: fleet at capacity — %d/%d workers already live. Wait for one to finish, or raise the ceiling: NEOMAX_FLEET_CAP=%d neomax ..."
                % (
                    live_now,
                    _usage_windows.FLEET_CONCURRENCY_CAP,
                    _usage_windows.FLEET_CONCURRENCY_CAP * 2,
                )
            )
            _config.sys.exit(3)
    profile = _selection.pick_account(selector, engine=engine)
    acct_no = _models.engine_profiles(engine).index(profile) + 1
    if plan_mode and goal:
        _models.err(
            "neomax: --goal is for WRITE workers; a --plan scout is read-only and can't iterate to a goal — use one or the other"
        )
        _config.sys.exit(2)
    if no_worktree:
        if want_pr:
            _models.err(
                "neomax: --pr needs an isolated worktree; %s is read-only/no-worktree — use one or the other"
                % ("--plan" if plan_mode else "--no-worktree")
            )
            _config.sys.exit(2)
        if base_ref:
            _models.err(
                "neomax: --base is ignored for a %s run (the scout reads the current checkout, not %s)"
                % ("--plan" if plan_mode else "--no-worktree", base_ref)
            )
        (workdir, repo, branch, base) = (_config.os.getcwd(), None, None, None)
    else:
        try:
            (workdir, repo, branch, base) = _gitops.make_worktree(runid, base_ref)
        except _gitops.NotIsolable as e:
            _models.err("neomax: refusing to run unisolated — %s" % e)
            _config.sys.exit(2)
    if want_pr and (not repo):
        _models.err("neomax: --pr requires a git repo with commits — ignoring --pr")
        want_pr = False
    rec = {
        "id": runid,
        "engine": engine,
        "model": model,
        "tag": tag,
        "goal": goal,
        "max_turns": max_turns,
        "prompt": prompt,
        "_prompt_to_send": prompt,
        "profile": profile,
        "workdir": workdir,
        "repo": repo,
        "worktree": workdir if repo else None,
        "branch": branch,
        "base": base,
        "base_ref": base_ref,
        "effort": effort,
        "ultra": ultra,
        "pr": want_pr,
        "plan_mode": plan_mode,
        "project": _projects.project_of(_config.os.getcwd()),
        "wall_min": wall_min,
        "stall_min": stall_min,
        "no_failover": no_failover,
        "attempt": 1,
        "status": "running",
        "started": int(_config.time.time()),
        "pid": _config.os.getpid(),
        "cwd": _config.os.getcwd(),
        "orch_session": _orchestrators.current_orch_session(),
        "session": str(_config.uuidlib.uuid4()) if engine == "claude" else None,
    }
    _tier = (
        _models.codex_model_tier(model)
        if engine == "codex"
        else next((k for (k, v) in _models.KIMI_MODELS.items() if v == model), None)
        if engine == "kimi"
        else None
    )
    label = engine + (
        "/" + _tier if _tier and _tier != _config.CODEX_MODEL_DEFAULT_TIER else ""
    )
    _models.err(
        "neomax → %s worker on %s (account %d%s%s) run=%s"
        % (
            label,
            profile,
            acct_no,
            " effort " + effort if effort else "",
            " [ultracode]" if ultra else "",
            runid,
        )
    )
    if rec["session"]:
        _models.err("NEOMAX SESSION id=%s" % rec["session"])
    _runs.log_event(
        rec, "dispatched", prompt=(prompt or "")[:120], ultra=ultra, pr=want_pr
    )
    if detach:
        _run_lifecycle.detach_supervisor(rec)
    else:
        _failover.run_with_failover(rec)


def cmd_resume(argv):
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import models as _models
    from . import premerge as _premerge
    from . import run_lifecycle as _run_lifecycle
    from . import runs as _runs
    from . import worker_commands as _worker_commands

    if not argv:
        _premerge.usage()
    rec = _runs.load_run(argv[0])
    if not rec:
        _models.err("neomax: unknown run %s" % argv[0])
        _config.sys.exit(1)
    engine = rec.get("engine", "claude")
    _run_lifecycle.refuse_if_other_orch(rec, "resume", argv)
    _run_lifecycle.refuse_if_worker_alive(rec, "resume")
    extra = (
        " ".join(argv[1:])
        or "Continue the task you were working on. If you were mid-way through changes, run `git status`, inspect, and finish the job."
    )
    rec["_prompt_to_send"] = extra
    rec["attempt"] = rec.get("attempt", 1) + 1
    rec["status"] = "running"
    rec["acknowledged"] = False
    rec["pid"] = _config.os.getpid()
    if not _config.os.path.isdir(rec["workdir"]):
        if not _run_lifecycle.ensure_workdir(rec):
            _models.err(
                "neomax: cannot resume — start fresh with: neomax retry %s" % rec["id"]
            )
            _config.sys.exit(1)
    _runs.log_event(rec, "resume")
    if engine == "codex":
        _codex_usage.remember_session(rec)
        rec["resumed"] = False
        rec["session"] = None
        _runs.save_run(rec)
        _models.err(
            "neomax: continuing codex run %s in worktree on %s (fresh thread, same worktree)"
            % (rec["id"], rec["profile"])
        )
        status = _worker_commands.execute(rec)
    else:
        if not rec.get("session"):
            _models.err(
                "neomax: run %s has no session id — use: neomax retry %s"
                % (rec["id"], rec["id"])
            )
            _config.sys.exit(1)
        rec["resumed"] = True
        _runs.save_run(rec)
        _models.err(
            "neomax: resuming run %s session %s on %s"
            % (rec["id"], rec["session"], rec["profile"])
        )
        status = _worker_commands.execute(rec, resume_session=rec["session"])
    _codex_usage.maybe_cooldown(rec, status)
    _codex_usage.finish(rec, status)


def cmd_retry(argv):
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import models as _models
    from . import premerge as _premerge
    from . import run_lifecycle as _run_lifecycle
    from . import runs as _runs
    from . import selection as _selection
    from . import worker_commands as _worker_commands

    if not argv:
        _premerge.usage()
    rec = _runs.load_run(argv[0])
    if not rec:
        _models.err("neomax: unknown run %s" % argv[0])
        _config.sys.exit(1)
    _run_lifecycle.refuse_if_other_orch(rec, "retry", argv)
    _run_lifecycle.refuse_if_worker_alive(rec, "retry")
    engine = rec.get("engine", "claude")
    selector = argv[1] if len(argv) > 1 else "auto"
    tried = rec.get("tried", [rec["profile"]])
    if selector == "auto":
        try:
            rec["profile"] = _selection.pick_account(
                "auto", exclude=set(tried), engine=engine
            )
        except SystemExit:
            _models.err(
                "neomax: every account already tried — picking least-busy regardless"
            )
            rec["tried"] = []
            rec["profile"] = _selection.pick_account("auto", engine=engine)
    else:
        rec["profile"] = _selection.pick_account(selector, engine=engine)
    rec["attempt"] = rec.get("attempt", 1) + 1
    _codex_usage.remember_session(rec)
    rec["session"] = str(_config.uuidlib.uuid4()) if engine == "claude" else None
    rec["resumed"] = False
    rec["_prompt_to_send"] = rec["prompt"] + _models.CONTINUATION_NOTE
    if rec.get("worktree") and (not _run_lifecycle.ensure_workdir(rec)):
        _models.err(
            "neomax: cannot retry — isolated worktree is gone and unrecoverable"
        )
        _config.sys.exit(1)
    rec["status"] = "running"
    rec["acknowledged"] = False
    rec["pid"] = _config.os.getpid()
    _runs.save_run(rec)
    _runs.log_event(rec, "retry", account=_config.os.path.basename(rec["profile"]))
    _models.err(
        "neomax: retrying run %s on %s (attempt %d)"
        % (rec["id"], rec["profile"], rec["attempt"])
    )
    status = _worker_commands.execute(rec)
    _codex_usage.maybe_cooldown(rec, status)
    _codex_usage.finish(rec, status)
