"""Account identity and ambient-status helpers."""


def profile_acct_no(profile, engine):
    """Real account number from a profile path (default → 1, …-acctN → N,
    the dedicated orchestrator profile → "orch")."""
    from . import config as _config
    from . import models as _models

    if profile == _models.ENGINES[engine]["default_dir"]:
        return 1
    if _models.orch_profile(engine) and profile == _models.orch_profile(engine):
        return "orch"
    m = _config.re.search("-acct(\\d+)$", profile)
    return int(m.group(1)) if m else 0


def account_identity(profile, engine):
    """Best-effort {email, plan, name} for an account, read from local config files
    (fast, no subprocess) so the dashboard can label each account. {} if unknown."""
    from . import account_auth as _account_auth
    from . import auth_rotation as _auth_rotation
    from . import config as _config

    try:
        if engine == "opencode":
            return {"email": None, "plan": "Go", "name": "OX Alpha Free"}
        if engine == "kimi":
            mode = _account_auth.kimi_auth_method(profile)
            return {
                "email": None,
                "plan": "Kimi Code",
                "name": "K3 / K2.7",
                "auth_method": mode,
            }
        if engine == "grok":
            with open(_config.os.path.join(profile, "auth.json")) as f:
                store = _config.json.load(f) or {}
            item = next(
                (v for v in store.values() if isinstance(v, dict) and v.get("key")), {}
            )
            raw_mode = str(item.get("auth_mode") or "").lower()
            mode = (
                "OAuth"
                if raw_mode in ("oidc", "oauth")
                else "API key"
                if raw_mode == "api_key"
                else raw_mode.replace("_", " ").title() or None
            )
            name = " ".join(
                (x for x in (item.get("first_name"), item.get("last_name")) if x)
            )
            return {
                "email": item.get("email"),
                "plan": item.get("team_name") or "xAI",
                "name": name or None,
                "auth_method": mode,
            }
        if engine == "codex":
            with open(_config.os.path.join(profile, "auth.json")) as f:
                d = _config.json.load(f)
            seg = ((d.get("tokens") or {}).get("id_token") or "").split(".")
            if len(seg) >= 2:
                pl = _config.json.loads(
                    _config.base64.urlsafe_b64decode(seg[1] + "=" * (-len(seg[1]) % 4))
                )
                plan = (pl.get("https://api.openai.com/auth") or {}).get(
                    "chatgpt_plan_type"
                )
                return {
                    "email": pl.get("email"),
                    "plan": (plan or "").capitalize() or None,
                    "name": pl.get("name"),
                }
        else:
            cfg = (
                _config.os.path.join(_config.HOME, ".claude.json")
                if profile == _config.os.path.join(_config.HOME, ".claude")
                else _config.os.path.join(profile, ".claude.json")
            )
            with open(cfg) as f:
                oa = _config.json.load(f).get("oauthAccount") or {}
            plan = (
                (oa.get("organizationRateLimitTier") or "")
                .replace("default_claude_", "")
                .replace("_", " ")
                .strip()
            )
            email = oa.get("emailAddress") or _auth_rotation.claude_account_email(
                profile
            )
            return {
                "email": email,
                "plan": plan.capitalize() or None,
                "name": oa.get("displayName"),
            }
    except Exception:
        pass
    return {}


def live_children(rec):
    """Sub-agents currently active under a RUNNING worker, parsed live from its stream
    log (sub-agents share the worker's account/quota). Returns the child list with live
    status; [] if the log is unreadable. Best-effort — never raises."""
    from . import config as _config
    from . import event_parsers as _event_parsers

    log = rec.get("log")
    if not log or not _config.os.path.isfile(log):
        return rec.get("children") or []
    try:
        ev = _event_parsers.parse_events(log, rec.get("engine", "claude"))
        return ev.get("children") or []
    except Exception:
        return rec.get("children") or []


def _solo_ambient(worker_worktrees=()):
    """Interactive-session ambient (main + sub-agents) for the `cmax solo` scratch profile
    (~/.claude-solo), which is NOT in engine_profiles — so without this a solo session's whole
    Workflow/sub-agent fleet is invisible to the dashboard. Returns (sessions, mains, subagents)."""
    from . import agent_activity as _agent_activity
    from . import ambient_sessions as _ambient_sessions
    from . import config as _config
    from . import projects as _projects
    from . import solo as _solo

    solo = _solo._solo_profile()
    if not _config.os.path.isdir(solo):
        return ([], 0, 0)
    main_det = _ambient_sessions.ambient_main_details(solo, "claude", worker_worktrees)
    details = [
        a
        for a in _agent_activity.ambient_agent_details(solo, "claude")
        if not a.get("worker")
    ]
    sess_map = {}

    def _sess(sid, src, label=None):
        return sess_map.setdefault(
            sid,
            {
                "engine": "claude",
                "account": "solo",
                "acct_no": "solo",
                "session": sid,
                "project": _projects.project_of(src.get("cwd")),
                "slug": src.get("slug"),
                "cwd": src.get("cwd"),
                "branch": src.get("branch"),
                "label": label,
                "age_s": src.get("age_s"),
                "agents": [],
                "solo": True,
            },
        )

    for mn in main_det:
        _sess(mn["session"], mn, label=mn.get("label"))
    for ag in details:
        _sess(ag["session"], ag)["agents"].append(
            {
                "label": ag["label"],
                "age_s": ag["age_s"],
                "wf": ag.get("wf"),
                "done": ag.get("done"),
                "working": ag.get("working"),
                "tok": ag.get("tok") or {"in": 0, "out": 0, "cache": 0},
            }
        )
    return (
        list(sess_map.values()),
        len(main_det),
        sum((1 for d in details if d.get("working"))),
    )
