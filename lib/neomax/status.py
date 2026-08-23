"""Unified account, run, and session status aggregation."""


def gather_status():
    """Full machine status for all engines — the single source of truth the web
    portal and any dashboard render. Read-only apart from refreshing the per-account
    usage cache (fetch_claude_usage)."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import agent_activity as _agent_activity
    from . import ambient_sessions as _ambient_sessions
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import credentials as _credentials
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import projects as _projects
    from . import provider_activity as _provider_activity
    from . import runs as _runs
    from . import status_helpers as _status_helpers
    from . import tasks as _tasks
    from . import telemetry_grok as _telemetry_grok
    from . import telemetry_kimi as _telemetry_kimi
    from . import telemetry_opencode as _telemetry_opencode
    from . import usage_aggregation as _usage_aggregation
    from . import usage_windows as _usage_windows

    now = int(_config.time.time())
    out = {"now": now, "engines": {}, "runs": [], "inbox": 0, "ambient": []}
    all_recs = _runs.all_runs()
    running_recs = [
        r
        for r in all_recs
        if _orchestrators.effective_status(r) in ("running", "orphaned")
    ]
    worker_worktrees = tuple(
        sorted({r.get("worktree") for r in running_recs if r.get("worktree")})
    )
    opencode_snaps = {
        p: _telemetry_opencode.opencode_profile_snapshot(p, 7)
        for p in _telemetry_opencode.opencode_telemetry_profiles()
    }
    kimi_snaps = {
        p: _telemetry_kimi.kimi_profile_snapshot(p, 7)
        for p in _telemetry_kimi.kimi_telemetry_profiles()
    }
    grok_snaps = {
        p: _telemetry_grok.grok_profile_snapshot(p, 7)
        for p in _telemetry_grok.grok_telemetry_profiles()
    }
    kids_by_profile = {}
    for r in running_recs:
        ch = _status_helpers.live_children(r)
        r["_live_children"] = ch
        active = [
            c for c in ch if c.get("status") == "running" and c.get("kind") != "step"
        ]
        kids_by_profile.setdefault(r.get("profile"), 0)
        kids_by_profile[r.get("profile")] += len(active)
    for engine in ("claude", "codex", "opencode", "kimi", "grok"):
        live = _account_state.live_counts(engine)
        accts = []
        profile_list = (
            _telemetry_opencode.opencode_telemetry_profiles()
            if engine == "opencode"
            else _telemetry_kimi.kimi_telemetry_profiles()
            if engine == "kimi"
            else _telemetry_grok.grok_telemetry_profiles()
            if engine == "grok"
            else _models.engine_profiles(engine)
        )
        for i, p in enumerate(profile_list):
            worker_eligible = _account_auth.logged_in(p, engine)
            connected = (
                bool(_account_auth.kimi_auth_method(p))
                if engine == "kimi"
                else worker_eligible
            )
            cd = _account_state.cooling_down(p)
            if engine == "claude":
                win = (
                    _usage_windows.fetch_claude_usage(p)
                    if _account_auth.logged_in(p, engine)
                    else None
                )
            elif engine == "codex":
                win = (
                    _codex_usage.fetch_codex_usage(p)
                    if _account_auth.logged_in(p, engine)
                    else None
                )
            else:
                win = None
            token_expired = isinstance(win, dict) and win.get("expired")
            if token_expired:
                win = None
            if win:
                for k in ("five_hour", "seven_day"):
                    w = win.get(k) or {}
                    if w.get("resets_at") and now >= w["resets_at"]:
                        w["used_percent"] = 0.0
            ident = _status_helpers.account_identity(p, engine)
            workers = sum((1 for r in running_recs if r.get("profile") == p))
            if engine == "opencode":
                details = _provider_activity.opencode_agent_details(
                    p, worker_worktrees, opencode_snaps.get(p)
                )
            elif engine == "kimi":
                details = _provider_activity.kimi_agent_details(
                    p, worker_worktrees, kimi_snaps.get(p)
                )
            elif engine == "grok":
                details = _provider_activity.grok_agent_details(
                    p, worker_worktrees, grok_snaps.get(p)
                )
            else:
                details = _agent_activity.ambient_agent_details(p, engine)
            subagents = max(
                kids_by_profile.get(p, 0), sum((1 for d in details if d.get("working")))
            )
            main_det = (
                _provider_activity.opencode_main_details(
                    p, worker_worktrees, opencode_snaps.get(p)
                )
                if engine == "opencode"
                else _provider_activity.kimi_main_details(
                    p, worker_worktrees, kimi_snaps.get(p)
                )
                if engine == "kimi"
                else _ambient_sessions.ambient_main_details(p, engine, worker_worktrees)
            )
            if engine == "grok":
                main_det = _provider_activity.grok_main_details(
                    p, worker_worktrees, grok_snaps.get(p)
                )
            mains = len(main_det)
            sess_map = {}

            def _sess(sid, src, label=None):
                return sess_map.setdefault(
                    sid,
                    {
                        "engine": engine,
                        "account": _config.os.path.basename(p),
                        "acct_no": _status_helpers.profile_acct_no(p, engine),
                        "session": sid,
                        "project": _projects.project_of(src.get("cwd")),
                        "slug": src.get("slug"),
                        "cwd": src.get("cwd"),
                        "branch": src.get("branch"),
                        "label": label,
                        "age_s": src.get("age_s"),
                        "model": src.get("model"),
                        "tok": src.get("tok"),
                        "agents": [],
                    },
                )

            for mn in main_det:
                _sess(mn["session"], mn, label=mn.get("label"))
            for ag in details:
                if ag.get("worker"):
                    continue
                _sess(ag["session"], ag)["agents"].append(
                    {
                        "label": ag["label"],
                        "age_s": ag["age_s"],
                        "wf": ag.get("wf"),
                        "done": ag.get("done"),
                        "working": ag.get("working"),
                        "agent": ag.get("agent"),
                        "model": ag.get("model"),
                        "files": ag.get("files") or [],
                        "tok": ag.get("tok") or {"in": 0, "out": 0, "cache": 0},
                    }
                )
            out["ambient"].extend(sess_map.values())
            fh_pct = ((win or {}).get("five_hour") or {}).get("used_percent") or 0
            wk_pct = ((win or {}).get("seven_day") or {}).get("used_percent") or 0
            local_snap = (
                opencode_snaps.get(p)
                if engine == "opencode"
                else kimi_snaps.get(p)
                if engine == "kimi"
                else grok_snaps.get(p)
                if engine == "grok"
                else None
            )
            local_telemetry = None
            if local_snap is not None:
                local_telemetry = {
                    k: local_snap.get(k)
                    for k in (
                        "available",
                        "source",
                        "database",
                        "db_bytes",
                        "window_days",
                        "totals",
                        "models",
                        "agents",
                        "tool_usage",
                        "last_error",
                        "error",
                    )
                }
            accts.append(
                {
                    "n": _status_helpers.profile_acct_no(p, engine),
                    "dir": p,
                    "name": _config.os.path.basename(p),
                    "role": "orchestrator"
                    if p == _models.orch_profile(engine)
                    else "worker",
                    "rotate_advised": bool(
                        worker_eligible
                        and _usage_windows.rotate_advice(fh_pct, wk_pct, engine)[0]
                    ),
                    "authenticated": connected,
                    "worker_eligible": worker_eligible,
                    "email": ident.get("email"),
                    "plan": ident.get("plan"),
                    "display_name": ident.get("name"),
                    "auth_method": ident.get("auth_method"),
                    "live": live.get(p, 0)
                    + (mains if engine in ("codex", "opencode", "kimi", "grok") else 0),
                    "workers": workers,
                    "mains": mains,
                    "subagents": subagents,
                    "agents": workers + mains + subagents,
                    "cooldown_until": int(cd) if cd else 0,
                    "paused": _account_state.is_paused(p),
                    "token_expired": bool(token_expired),
                    "usage": win,
                    "telemetry": local_telemetry,
                }
            )
        seen = {}
        for a in accts:
            em = a.get("email")
            if a["authenticated"] and em:
                seen.setdefault(em, []).append(a)
        for em, group in seen.items():
            if len(group) > 1:
                for a in group:
                    a["duplicate_of"] = sorted((x["n"] for x in group))
        out["engines"][engine] = {"accounts": accts}
    RUNNING = ("running", "orphaned")
    for rec in all_recs:
        st = _orchestrators.effective_status(rec)
        if _orchestrators.in_inbox(rec):
            out["inbox"] += 1
        out["runs"].append(
            {
                "id": rec["id"],
                "engine": rec.get("engine", "claude"),
                "account": _config.os.path.basename(rec.get("profile", "")),
                "acct_no": _status_helpers.profile_acct_no(
                    rec.get("profile", ""), rec.get("engine", "claude")
                ),
                "status": st,
                "prompt": (rec.get("prompt") or "")[:160],
                "branch": rec.get("branch"),
                "session": rec.get("session"),
                "repo": _config.os.path.basename(rec.get("repo") or "") or None,
                "children": len(rec.get("_live_children", rec.get("children") or [])),
                "child_list": rec.get("_live_children", rec.get("children") or [])[:24],
                "effort": rec.get("effort"),
                "ultra": bool(rec.get("ultra")),
                "opus": bool(rec.get("opus")),
                "model": rec.get("model"),
                "tag": (rec.get("tag") or "")[:120] or None,
                "attempt": rec.get("attempt", 1),
                "goal": (rec.get("goal") or "")[:300] or None,
                "pr_url": rec.get("pr_url"),
                "acknowledged": bool(rec.get("acknowledged")),
                "worktree": rec.get("worktree"),
                "worktree_state": rec.get("worktree_state"),
                "project": rec.get("project")
                or _projects.project_of(rec.get("repo") or rec.get("cwd")),
                "files_touched": rec.get("files_touched") or [],
                "started": rec.get("started", 0),
                "ended": rec.get("ended"),
                "orch_session": rec.get("orch_session"),
            }
        )
    out["runs"].sort(key=lambda r: r["started"], reverse=True)
    (solo_sessions, solo_mains, solo_subs) = _status_helpers._solo_ambient(
        worker_worktrees
    )
    out["ambient"].extend(solo_sessions)
    accts_all = [a for e in out["engines"].values() for a in e["accounts"]]
    workers_total = sum((a["workers"] for a in accts_all))
    mains_total = sum((a.get("mains", 0) for a in accts_all)) + solo_mains
    subagents_total = sum((a["subagents"] for a in accts_all)) + solo_subs
    oc7 = {
        k: 0
        for k in (
            "in",
            "out",
            "reasoning",
            "cw",
            "cr",
            "requests",
            "completions",
            "unfinished",
            "errors",
            "rate_limits",
            "sessions",
            "main_sessions",
            "native_subagents",
            "tool_calls",
            "tool_errors",
            "files",
            "adds",
            "dels",
        )
    }
    for a in out["engines"].get("opencode", {}).get("accounts", []):
        totals = (a.get("telemetry") or {}).get("totals") or {}
        for k in oc7:
            oc7[k] += _telemetry_opencode._opencode_int(totals.get(k))
    oc7["last_activity"] = max(
        [
            ((a.get("telemetry") or {}).get("totals") or {}).get("last_activity") or 0
            for a in out["engines"].get("opencode", {}).get("accounts", [])
        ]
        or [0]
    )
    kimi7 = {
        k: 0
        for k in (
            "in",
            "out",
            "cw",
            "cr",
            "requests",
            "completions",
            "errors",
            "rate_limits",
            "sessions",
            "main_sessions",
            "native_subagents",
            "tool_calls",
            "tool_errors",
            "files",
        )
    }
    for a in out["engines"].get("kimi", {}).get("accounts", []):
        totals = (a.get("telemetry") or {}).get("totals") or {}
        for k in kimi7:
            kimi7[k] += _telemetry_opencode._opencode_int(totals.get(k))
    grok7 = {
        k: 0
        for k in (
            "in",
            "out",
            "reasoning",
            "cw",
            "cr",
            "requests",
            "completions",
            "errors",
            "rate_limits",
            "sessions",
            "main_sessions",
            "native_subagents",
            "tool_calls",
            "tool_errors",
            "files",
        )
    }
    for a in out["engines"].get("grok", {}).get("accounts", []):
        totals = (a.get("telemetry") or {}).get("totals") or {}
        for k in grok7:
            grok7[k] += _telemetry_opencode._opencode_int(totals.get(k))

    def weekly_min(engine):
        vals = [
            ((a.get("usage") or {}).get("seven_day") or {}).get("used_percent")
            for a in out["engines"][engine]["accounts"]
            if a["authenticated"]
        ]
        vals = [v for v in vals if v is not None]
        return round(min(vals), 1) if vals else None

    (cw, xw, ow, kw, gw) = (
        weekly_min("claude"),
        weekly_min("codex"),
        weekly_min("opencode"),
        weekly_min("kimi"),
        weekly_min("grok"),
    )
    if out["ambient"]:
        try:
            by_sess = {}
            for r in _usage_aggregation.gather_usage(3).get("by_session") or []:
                sid = r.get("session")
                if not sid:
                    continue
                b = by_sess.setdefault(sid, {"in": 0, "out": 0, "cache": 0})
                b["in"] += r.get("in", 0)
                b["out"] += r.get("out", 0)
                b["cache"] += r.get("cw", 0) + r.get("cr", 0)
            for sess in out["ambient"]:
                t = by_sess.get(sess.get("session"), {"in": 0, "out": 0, "cache": 0})
                ags = sess.get("agents", [])
                separate_children = sess.get("engine") in ("opencode", "kimi", "grok")
                si = (
                    0
                    if separate_children
                    else sum(((a.get("tok") or {}).get("in", 0) for a in ags))
                )
                so = (
                    0
                    if separate_children
                    else sum(((a.get("tok") or {}).get("out", 0) for a in ags))
                )
                sc = (
                    0
                    if separate_children
                    else sum(((a.get("tok") or {}).get("cache", 0) for a in ags))
                )
                sess["tok"] = {
                    "in": max(t["in"] - si, 0),
                    "out": max(t["out"] - so, 0),
                    "cache": max(t["cache"] - sc, 0),
                }
        except Exception:
            pass
    out["orchestrators"] = _orchestrators.all_orchestrators()
    out["tasks"] = list(_tasks.load_tasks()["tasks"].values())
    out["summary"] = {
        "live_total": sum((a["live"] for a in accts_all)),
        "running": sum((1 for r in out["runs"] if r["status"] in RUNNING)),
        "workers": workers_total,
        "mains": mains_total,
        "subagents": subagents_total,
        "agents_total": workers_total + mains_total + subagents_total,
        "accounts_up": sum((1 for a in accts_all if a["authenticated"])),
        "accounts_total": len(accts_all),
        "cooling": sum((1 for a in accts_all if a["cooldown_until"] > now)),
        "inbox": out["inbox"],
        "tasks_open": sum(
            (1 for t in out["tasks"] if t.get("status") in _tasks.TASK_OPEN_STATUSES)
        ),
        "runs_total": len(out["runs"]),
        "opencode_7d": oc7,
        "kimi_7d": kimi7,
        "grok_7d": grok7,
        "claude_weekly_min": cw,
        "codex_weekly_min": xw,
        "opencode_weekly_min": ow,
        "kimi_weekly_min": kw,
        "grok_weekly_min": gw,
        "claude_weekly_soft": bool(
            cw is not None and cw >= _usage_windows.SEVEN_D_SKIP
        ),
        "codex_weekly_soft": bool(xw is not None and xw >= _usage_windows.SEVEN_D_SKIP),
        "claude_weekly_exhausted": bool(
            cw is not None and cw >= _usage_windows.SEVEN_D_HARD
        ),
        "codex_weekly_exhausted": bool(
            xw is not None and xw >= _usage_windows.SEVEN_D_HARD
        ),
        "opencode_weekly_soft": False,
        "opencode_weekly_exhausted": False,
        "kimi_weekly_soft": False,
        "kimi_weekly_exhausted": False,
        "grok_weekly_soft": False,
        "grok_weekly_exhausted": False,
        "fleet_scope": sorted(_config.allowed_engines()),
        "orch_reserved": _models.orch_reserved(),
        "auth_rotations": _credentials.recent_auth_rotations(
            within_s=6 * 3600, limit=6
        ),
        "rotate_advised": [
            {"engine": e, "n": a["n"], "live": a["live"], "agents": a["agents"]}
            for (e, eng) in out["engines"].items()
            for a in eng["accounts"]
            if a.get("rotate_advised")
        ],
        "orch_accounts": {
            e: bool(_models.orch_profile(e))
            for e in ("claude", "codex", "opencode", "kimi", "grok")
        },
        "duplicate_accounts": sorted(
            {a["email"] for a in accts_all if a.get("duplicate_of") and a.get("email")}
        ),
    }
    return out
