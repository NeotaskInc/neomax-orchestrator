"""Account pauses and orchestrator controls."""


def cmd_pause(argv, paused=True):
    """Pause / unpause an account — a paused account is HARD-EXCLUDED from auto-dispatch (no
    tasks routed to it) until unpaused, regardless of its usage %. LIVE: pick_account reads it
    each dispatch, so it takes effect on the next task with NO orchestrator restart. Keeps the
    account logged in (use it again by unpausing). An explicit `auto N` still overrides.
       neomax pause   <N|orch|all> [--engine claude|codex|opencode|kimi|grok]
       neomax unpause <N|orch|all> [--engine claude|codex|opencode|kimi|grok]"""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import credentials as _credentials
    from . import models as _models
    from . import status_helpers as _status_helpers

    engine = "claude"
    if "--engine" in argv:
        i = argv.index("--engine")
        engine = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
    if engine not in _models.ENGINES:
        _models.err("neomax: unknown engine %r" % engine)
        _config.sys.exit(2)
    sel = next((a for a in argv if not a.startswith("-")), None)
    verb = "pause" if paused else "unpause"
    if not sel:
        _models.err(
            "neomax: %s <N|orch|all> [--engine claude|codex|opencode|kimi|grok]" % verb
        )
        _config.sys.exit(2)
    if sel.lower() == "all":
        targets = [
            p
            for p in _models.engine_profiles(engine)
            if _account_auth.logged_in(p, engine)
        ]
    else:
        p = _credentials._resolve_profile(sel, engine)
        if not p:
            _models.err("neomax: cannot resolve %s account %r" % (engine, sel))
            _config.sys.exit(2)
        targets = [p]
    if not targets:
        _models.err("neomax: no %s account to %s" % (engine, verb))
        _config.sys.exit(1)
    for p in targets:
        _account_state.set_paused(p, paused)
        _models.err(
            "neomax: %s %s account %s (%s)%s"
            % (
                "PAUSED" if paused else "UNPAUSED",
                engine,
                _status_helpers.profile_acct_no(p, engine),
                _config.os.path.basename(p),
                " — no tasks will route here until unpaused" if paused else "",
            )
        )


def cmd_paused(argv):
    """List paused accounts (excluded from auto-dispatch).  neomax paused [--json]"""
    from . import account_state as _account_state
    from . import config as _config
    from . import models as _models
    from . import status_helpers as _status_helpers

    rows = []
    for engine in ("claude", "codex", "opencode", "kimi", "grok"):
        for p in _models.engine_profiles(engine):
            if _account_state.is_paused(p):
                rows.append(
                    {
                        "engine": engine,
                        "n": _status_helpers.profile_acct_no(p, engine),
                        "account": _config.os.path.basename(p),
                    }
                )
    if "--json" in argv:
        print(_config.json.dumps(rows))
        return
    if not rows:
        print("no paused accounts — all are eligible for dispatch")
        return
    print("PAUSED accounts (held out of auto-dispatch until `neomax unpause`):")
    for r in rows:
        print("  %s account %s (%s)" % (r["engine"], r["n"], r["account"]))


def cmd_orch_register(argv):
    """Register/refresh THIS orchestrator session in the shared registry. Called by the
    active provider launcher (reads NEOMAX_ORCH_SESSION / NEOMAX_ORCH_PID / NEOMAX_ROLE / cwd from env).
       neomax orch-register [--session ID] [--pid N]"""
    from . import config as _config
    from . import handoff as _handoff
    from . import models as _models
    from . import orchestrators as _orchestrators

    session = None
    pid = None
    for i, a in enumerate(argv):
        if a == "--session" and i + 1 < len(argv):
            session = argv[i + 1]
        elif a == "--pid" and i + 1 < len(argv):
            try:
                pid = int(argv[i + 1])
            except ValueError:
                pass
    session = session or _config.os.environ.get("NEOMAX_ORCH_SESSION")
    pid = pid or (
        int(_config.os.environ["NEOMAX_ORCH_PID"])
        if _config.os.environ.get("NEOMAX_ORCH_PID", "").isdigit()
        else None
    )
    _orchestrators.register_orchestrator(
        session, pid, _handoff.orchestrator_engine(), reserved=_models.orch_reserved()
    )


def cmd_orch_unregister(argv):
    """De-register an orchestrator (on clean exit).  neomax orch-unregister [--session ID]"""
    from . import config as _config
    from . import orchestrators as _orchestrators

    session = None
    for i, a in enumerate(argv):
        if a == "--session" and i + 1 < len(argv):
            session = argv[i + 1]
    _orchestrators.unregister_orchestrator(
        session or _config.os.environ.get("NEOMAX_ORCH_SESSION")
    )


def cmd_orchestrators(argv):
    """Show the OTHER (and this) interactive orchestrator sessions running on this machine —
    so concurrent orchestrators know who else is working, on what project / branch namespace,
    and don't step on each other's runs/merges.  neomax orchestrators [--json] [--all]"""
    from . import config as _config
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    show_all = "--all" in argv
    orchs = (
        _orchestrators.all_orchestrators()
        if show_all
        else _orchestrators.live_orchestrators()
    )
    me = _orchestrators.current_orch_session()
    now = int(_config.time.time())
    running = [
        r
        for r in _runs.all_runs()
        if _orchestrators.effective_status(r) in ("running", "orphaned")
    ]
    inflight = {}
    for r in running:
        inflight[r.get("orch_session")] = inflight.get(r.get("orch_session"), 0) + 1
    rows = []
    for o in orchs:
        rows.append(
            {
                **o,
                "is_me": o.get("session") == me,
                "runs_in_flight": inflight.get(o.get("session"), 0),
                "idle_s": now - o.get("last_seen", now),
            }
        )
    if "--json" in argv:
        print(_config.json.dumps(rows))
        return
    if not rows:
        print(
            "no other orchestrator sessions registered (you're the only one, or none running)"
        )
        return
    print(
        "ORCHESTRATOR SESSIONS (concurrent Neomax launchers — coordinate so you don't overwrite each other):"
    )
    for o in rows:
        tag = (
            " ← THIS SESSION"
            if o["is_me"]
            else ""
            if o["live"]
            else "  (dead — stale entry)"
        )
        proj = o.get("project") or "multi/none"
        pref = "`%s/`" % o["branch_prefix"] if o.get("branch_prefix") else "no-prefix"
        print(
            "  %s acct %s · project %s · branch %s · %d run(s) in flight · idle %dm%s"
            % (
                o.get("engine"),
                o.get("account"),
                proj,
                pref,
                o.get("runs_in_flight", 0),
                o.get("idle_s", 0) // 60,
                tag,
            )
        )
    others = [o for o in rows if not o["is_me"] and o["live"]]
    if others and me:
        print(
            "\n  ⚠ %d OTHER live orchestrator(s) are working. Before you merge to main, `git fetch` and check main didn't move; never ack/clean/kill their runs (`neomax ls`/`clean`/`kill` now refuse another orchestrator's runs without --any)."
            % len(others)
        )
