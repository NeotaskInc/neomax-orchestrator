"""Interactive session history and reporting."""


def gather_sessions(days=3, limit=60):
    """Recent MAIN sessions per profile (all five engines) — by-session history of work
    done OUTSIDE the orchestrator (the run ledger covers neomax-dispatched runs only).
    Each: engine/acct/session/cwd/branch/label/started/last_active/active + tokens/cost
    joined from the usage ledger when available."""
    from . import config as _config
    from . import http_client as _http_client
    from . import models as _models
    from . import projects as _projects
    from . import provider_activity as _provider_activity
    from . import runs as _runs
    from . import session_headers as _session_headers
    from . import status_helpers as _status_helpers
    from . import telemetry_grok as _telemetry_grok
    from . import telemetry_kimi as _telemetry_kimi
    from . import telemetry_opencode as _telemetry_opencode
    from . import usage_aggregation as _usage_aggregation

    cutoff = _config.time.time() - days * 86400
    cost_by_sess = {}
    try:
        for r in _usage_aggregation.gather_usage(days + 1).get("by_session") or []:
            cost_by_sess[r.get("provider"), r.get("session")] = r
    except Exception:
        pass
    out = []
    for engine in ("claude", "codex"):
        for p in _models.engine_profiles(engine):
            pat = (
                _config.os.path.join(p, "projects", "*", "*.jsonl")
                if engine == "claude"
                else _config.os.path.join(p, "sessions", "**", "rollout-*.jsonl")
            )
            for f in _config.globmod.iglob(pat, recursive=engine != "claude"):
                try:
                    mt = _config.os.path.getmtime(f)
                    if mt < cutoff:
                        continue
                    size = _config.os.path.getsize(f)
                    with open(f, "rb") as fh:
                        head = fh.read(262144).decode("utf-8", "replace")
                        fh.seek(max(0, size - 65536))
                        tail = fh.read().decode("utf-8", "replace")
                except OSError:
                    continue
                meta = (
                    _session_headers._claude_head_meta
                    if engine == "claude"
                    else _session_headers._codex_head_meta
                )(head)
                if (meta.get("cwd") or "").startswith(_config.STATE_DIR):
                    continue
                base = _config.os.path.basename(f)[:-6]
                sess = (
                    base
                    if engine == "claude"
                    else base[-36:]
                    if len(base) >= 36
                    else base
                )
                first_ts = None
                try:
                    first_ts = _http_client.iso_to_epoch(
                        (_config.json.loads(head.split("\n", 1)[0]) or {}).get(
                            "timestamp"
                        )
                    )
                except (ValueError, AttributeError):
                    pass
                active = (
                    _session_headers._claude_tail_active
                    if engine == "claude"
                    else _session_headers._codex_tail_active
                )(tail)
                u = cost_by_sess.get((engine, sess)) or {}
                out.append(
                    {
                        "engine": engine,
                        "acct_no": _status_helpers.profile_acct_no(p, engine),
                        "account": _config.os.path.basename(p),
                        "session": sess,
                        "project": _projects.project_of(meta.get("cwd")),
                        "cwd": meta.get("cwd"),
                        "branch": meta.get("branch"),
                        "label": meta.get("label"),
                        "started": int(first_ts) if first_ts else None,
                        "last_active": int(mt),
                        "active": bool(active),
                        "completions": u.get("completions"),
                        "requests": u.get("requests"),
                        "in": u.get("in"),
                        "out": u.get("out"),
                        "reasoning": u.get("reasoning"),
                        "cache": u.get("cw", 0) + u.get("cr", 0),
                        "cost": u.get("cost"),
                    }
                )
    worker_sessions = {
        r.get("session")
        for r in _runs.all_runs()
        if r.get("engine") == "opencode" and r.get("session")
    }
    worker_cwds = tuple(
        (
            r.get("worktree")
            for r in _runs.all_runs()
            if r.get("engine") == "opencode" and r.get("worktree")
        )
    )
    for p in _telemetry_opencode.opencode_telemetry_profiles():
        snap = _telemetry_opencode.opencode_profile_snapshot(p, days)
        live_ids = {
            d.get("session")
            for d in _provider_activity.opencode_main_details(p, worker_cwds, snap)
        }
        for s in snap.get("sessions") or []:
            if s.get("parent_id") or s.get("id") in worker_sessions:
                continue
            cwd = s.get("cwd") or ""
            if (
                cwd.startswith(_config.STATE_DIR)
                or _config.os.sep + "worktrees" + _config.os.sep in cwd
                or (worker_cwds and cwd.startswith(worker_cwds))
            ):
                continue
            sess = s.get("id")
            u = cost_by_sess.get(("opencode", sess)) or {}
            tok = s.get("tokens") or {}
            out.append(
                {
                    "engine": "opencode",
                    "acct_no": _status_helpers.profile_acct_no(p, "opencode"),
                    "account": _config.os.path.basename(p),
                    "session": sess,
                    "project": _projects.project_of(cwd),
                    "cwd": cwd,
                    "branch": None,
                    "label": s.get("title"),
                    "model": s.get("model"),
                    "started": s.get("started"),
                    "last_active": s.get("last_active"),
                    "active": bool(s.get("active") or sess in live_ids),
                    "completions": u.get("completions", s.get("completions")),
                    "requests": u.get("requests", s.get("requests")),
                    "errors": u.get("errors", s.get("errors")),
                    "rate_limits": u.get("rate_limits", s.get("rate_limits")),
                    "tool_calls": s.get("tool_calls", 0),
                    "tool_errors": s.get("tool_errors", 0),
                    "in": u.get("in", tok.get("in", 0)),
                    "out": u.get("out", tok.get("out", 0)),
                    "reasoning": u.get("reasoning", tok.get("reasoning", 0)),
                    "cache": u.get("cw", tok.get("cw", 0))
                    + u.get("cr", tok.get("cr", 0)),
                    "cost": u.get("cost", s.get("cost", 0.0)),
                }
            )
    kimi_worker_sessions = {
        r.get("session")
        for r in _runs.all_runs()
        if r.get("engine") == "kimi" and r.get("session")
    }
    for p in _telemetry_kimi.kimi_telemetry_profiles():
        snap = _telemetry_kimi.kimi_profile_snapshot(p, days)
        for s in snap.get("sessions") or []:
            if s.get("id") in kimi_worker_sessions:
                continue
            cwd = s.get("cwd") or ""
            if (
                cwd.startswith(_config.STATE_DIR)
                or _config.os.sep + "worktrees" + _config.os.sep in cwd
            ):
                continue
            sess = s.get("id")
            u = cost_by_sess.get(("kimi", sess)) or {}
            tok = s.get("tokens") or {}
            out.append(
                {
                    "engine": "kimi",
                    "acct_no": _status_helpers.profile_acct_no(p, "kimi"),
                    "account": _config.os.path.basename(p),
                    "session": sess,
                    "project": _projects.project_of(cwd),
                    "cwd": cwd,
                    "branch": None,
                    "label": s.get("title"),
                    "model": s.get("model"),
                    "started": s.get("started"),
                    "last_active": s.get("last_active"),
                    "active": bool(s.get("active")),
                    "completions": u.get("completions", s.get("completions")),
                    "requests": u.get("requests", s.get("requests")),
                    "errors": u.get("errors", s.get("errors")),
                    "rate_limits": u.get("rate_limits", s.get("rate_limits")),
                    "tool_calls": s.get("tool_calls", 0),
                    "tool_errors": s.get("tool_errors", 0),
                    "in": u.get("in", tok.get("in", 0)),
                    "out": u.get("out", tok.get("out", 0)),
                    "reasoning": 0,
                    "cache": u.get("cw", tok.get("cw", 0))
                    + u.get("cr", tok.get("cr", 0)),
                    "cost": 0.0,
                }
            )
    grok_worker_sessions = {
        r.get("session")
        for r in _runs.all_runs()
        if r.get("engine") == "grok" and r.get("session")
    }
    for p in _telemetry_grok.grok_telemetry_profiles():
        snap = _telemetry_grok.grok_profile_snapshot(p, days)
        for s in snap.get("sessions") or []:
            if s.get("parent_id") or s.get("id") in grok_worker_sessions:
                continue
            cwd = s.get("cwd") or ""
            if (
                cwd.startswith(_config.STATE_DIR)
                or _config.os.sep + "worktrees" + _config.os.sep in cwd
            ):
                continue
            sess = s.get("id")
            u = cost_by_sess.get(("grok", sess)) or {}
            tok = s.get("tokens") or {}
            out.append(
                {
                    "engine": "grok",
                    "acct_no": _status_helpers.profile_acct_no(p, "grok"),
                    "account": _config.os.path.basename(p),
                    "session": sess,
                    "project": _projects.project_of(cwd),
                    "cwd": cwd,
                    "branch": s.get("branch"),
                    "label": s.get("title"),
                    "model": s.get("model"),
                    "started": s.get("started"),
                    "last_active": s.get("last_active"),
                    "active": bool(s.get("active")),
                    "completions": u.get("completions", s.get("completions")),
                    "requests": u.get("requests", s.get("requests")),
                    "errors": u.get("errors", s.get("errors")),
                    "rate_limits": u.get("rate_limits", s.get("rate_limits")),
                    "tool_calls": s.get("tool_calls", 0),
                    "tool_errors": s.get("tool_errors", 0),
                    "in": u.get("in", tok.get("in", 0)),
                    "out": u.get("out", tok.get("out", 0)),
                    "reasoning": u.get("reasoning", tok.get("reasoning", 0)),
                    "cache": u.get("cw", tok.get("cw", 0))
                    + u.get("cr", tok.get("cr", 0)),
                    "cost": u.get("cost", s.get("cost", 0.0)),
                }
            )
    out.sort(key=lambda r: -r["last_active"])
    return out[:limit]


def cmd_sessions(argv):
    """Recent interactive sessions (by session, all engines) — the history of work done
    OUTSIDE the orchestrator. `neomax sessions [--json] [--days N]` (default 3)."""
    from . import config as _config

    days = 3
    if "--days" in argv:
        try:
            days = int(argv[argv.index("--days") + 1])
        except (ValueError, IndexError):
            pass
    rows = gather_sessions(days)
    if "--json" in argv:
        print(_config.json.dumps(rows))
        return
    print(
        "INTERACTIVE SESSIONS — last %dd (run ledger covers orchestrator-dispatched runs; this is everything else)"
        % days
    )
    for r in rows:
        print(
            "  %s %s acct%s %s · %s · %s%s · %s"
            % (
                _config.time.strftime(
                    "%m-%d %H:%M", _config.time.localtime(r["last_active"])
                ),
                r["engine"][:3],
                r["acct_no"],
                r["session"][:8],
                (r.get("cwd") or "?").split("/")[-1] or "?",
                "ACTIVE" if r["active"] else "idle",
                " · $%.2f/%s compl" % (r["cost"], r["completions"])
                if r.get("cost")
                else "",
                (r.get("label") or "")[:70],
            )
        )
