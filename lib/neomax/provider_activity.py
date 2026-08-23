"""OpenCode, Kimi, and Grok interactive activity."""


def opencode_main_details(profile, exclude_cwds=None, snapshot=None):
    """Live OpenCode root sessions plus registered ocmax sessions."""
    from . import config as _config
    from . import orchestrators as _orchestrators
    from . import telemetry_opencode as _telemetry_opencode

    snap = snapshot or _telemetry_opencode.opencode_profile_snapshot(profile, 3)
    exclude_cwds = tuple((c for c in exclude_cwds or () if c))
    roots = [s for s in snap.get("sessions") or [] if not s.get("parent_id")]

    def excluded(cwd):
        cwd = cwd or ""
        return (
            cwd.startswith(_config.STATE_DIR)
            or (exclude_cwds and cwd.startswith(exclude_cwds))
            or _config.os.sep + "worktrees" + _config.os.sep in cwd
        )

    chosen = {}
    for s in roots:
        if s.get("active") and (not s.get("archived")) and (not excluded(s.get("cwd"))):
            chosen[s["id"]] = s
    for orch in _orchestrators.orchestrators_on_account(profile, "opencode"):
        candidates = [
            s
            for s in roots
            if not excluded(s.get("cwd"))
            and (not orch.get("cwd") or s.get("cwd") == orch.get("cwd"))
            and (s.get("started", 0) >= orch.get("started", 0) - 90)
        ]
        if not candidates:
            candidates = [
                s
                for s in roots
                if not excluded(s.get("cwd"))
                and (not orch.get("cwd") or s.get("cwd") == orch.get("cwd"))
            ]
        if candidates:
            match = max(candidates, key=lambda s: s.get("last_active", 0))
            chosen[match["id"]] = dict(match, active=True, orchestrator=True)
        else:
            sid = str(orch.get("session") or "orch-opencode")
            chosen[sid] = {
                "id": sid,
                "cwd": orch.get("cwd"),
                "title": "OpenCode orchestrator",
                "started": orch.get("started"),
                "last_active": orch.get("last_seen"),
                "active": True,
                "orchestrator": True,
                "model": orch.get("model"),
                "tokens": {"in": 0, "out": 0, "reasoning": 0, "cr": 0, "cw": 0},
            }
    now = _config.time.time()
    out = []
    for s in chosen.values():
        tok = s.get("tokens") or {}
        out.append(
            {
                "session": s.get("id"),
                "cwd": s.get("cwd"),
                "branch": None,
                "slug": None,
                "label": s.get("title"),
                "model": s.get("model"),
                "age_s": max(0, int(now - (s.get("last_active") or now))),
                "tok": {
                    "in": tok.get("in", 0),
                    "out": tok.get("out", 0),
                    "cache": tok.get("cr", 0) + tok.get("cw", 0),
                },
                "orchestrator": bool(s.get("orchestrator")),
            }
        )
    return sorted(out, key=lambda x: x.get("age_s", 0))


def opencode_agent_details(profile, exclude_cwds=None, snapshot=None):
    """Currently working native OpenCode child sessions, with their own tokens/files."""
    from . import config as _config
    from . import telemetry_opencode as _telemetry_opencode

    snap = snapshot or _telemetry_opencode.opencode_profile_snapshot(profile, 3)
    exclude_cwds = tuple((c for c in exclude_cwds or () if c))
    now = _config.time.time()
    out = []
    for s in snap.get("sessions") or []:
        if not s.get("parent_id") or not s.get("active") or s.get("archived"):
            continue
        cwd = s.get("cwd") or ""
        worker = (
            cwd.startswith(_config.STATE_DIR)
            or (exclude_cwds and cwd.startswith(exclude_cwds))
            or _config.os.sep + "worktrees" + _config.os.sep in cwd
        )
        tok = s.get("tokens") or {}
        out.append(
            {
                "session": s.get("parent_id"),
                "agent": s.get("id"),
                "slug": None,
                "cwd": cwd,
                "branch": None,
                "label": s.get("title") or s.get("agent") or "OpenCode native subagent",
                "age_s": max(0, int(now - (s.get("last_active") or now))),
                "wf": None,
                "working": True,
                "done": False,
                "worker": worker,
                "model": s.get("model"),
                "files": s.get("files") or [],
                "tok": {
                    "in": tok.get("in", 0),
                    "out": tok.get("out", 0),
                    "cache": tok.get("cr", 0) + tok.get("cw", 0),
                },
            }
        )
    return out


def kimi_main_details(profile, exclude_cwds=None, snapshot=None):
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import telemetry_kimi as _telemetry_kimi

    snap = snapshot or _telemetry_kimi.kimi_profile_snapshot(profile, 3)
    exclude_cwds = tuple((c for c in exclude_cwds or () if c))
    now = _config.time.time()
    chosen = {}
    for s in snap.get("sessions") or []:
        cwd = s.get("cwd") or ""
        if (
            not s.get("active")
            or cwd.startswith(_config.STATE_DIR)
            or (exclude_cwds and cwd.startswith(exclude_cwds))
            or (_config.os.sep + "worktrees" + _config.os.sep in cwd)
        ):
            continue
        chosen[s.get("id")] = s
    for orch in _orchestrators.orchestrators_on_account(profile, "kimi"):
        candidates = [
            s
            for s in snap.get("sessions") or []
            if not orch.get("cwd") or s.get("cwd") == orch.get("cwd")
        ]
        if candidates:
            match = max(candidates, key=lambda s: s.get("last_active", 0))
            chosen[match.get("id")] = dict(match, active=True, orchestrator=True)
        else:
            sid = str(orch.get("session") or "orch-kimi")
            chosen[sid] = {
                "id": sid,
                "cwd": orch.get("cwd"),
                "title": "Kimi orchestrator",
                "last_active": orch.get("last_seen"),
                "model": orch.get("model") or _models.KIMI_MODEL,
                "orchestrator": True,
                "tokens": {},
            }
    out = []
    for s in chosen.values():
        cwd = s.get("cwd") or ""
        tok = s.get("tokens") or {}
        out.append(
            {
                "session": s.get("id"),
                "cwd": cwd,
                "branch": None,
                "slug": None,
                "label": s.get("title"),
                "model": s.get("model"),
                "age_s": max(0, int(now - (s.get("last_active") or now))),
                "tok": {
                    "in": tok.get("in", 0),
                    "out": tok.get("out", 0),
                    "cache": tok.get("cw", 0) + tok.get("cr", 0),
                },
                "orchestrator": bool(s.get("orchestrator")),
            }
        )
    return out


def kimi_agent_details(profile, exclude_cwds=None, snapshot=None):
    from . import config as _config
    from . import telemetry_kimi as _telemetry_kimi

    snap = snapshot or _telemetry_kimi.kimi_profile_snapshot(profile, 3)
    exclude_cwds = tuple((c for c in exclude_cwds or () if c))
    now = _config.time.time()
    out = []
    for s in snap.get("sessions") or []:
        cwd = s.get("cwd") or ""
        worker = (
            cwd.startswith(_config.STATE_DIR)
            or (exclude_cwds and cwd.startswith(exclude_cwds))
            or _config.os.sep + "worktrees" + _config.os.sep in cwd
        )
        for a in s.get("agents") or []:
            tok = a.get("tokens") or {}
            out.append(
                {
                    "session": s.get("id"),
                    "agent": a.get("id"),
                    "slug": None,
                    "cwd": cwd,
                    "branch": None,
                    "label": a.get("id") or "Kimi subagent",
                    "age_s": max(0, int(now - (a.get("last_active") or now))),
                    "wf": None,
                    "working": bool(a.get("active")),
                    "done": not a.get("active"),
                    "worker": worker,
                    "model": a.get("model"),
                    "files": a.get("files") or [],
                    "tok": {
                        "in": tok.get("in", 0),
                        "out": tok.get("out", 0),
                        "cache": tok.get("cw", 0) + tok.get("cr", 0),
                    },
                }
            )
    return out


def grok_main_details(profile, exclude_cwds=None, snapshot=None):
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import telemetry_grok as _telemetry_grok

    snap = snapshot or _telemetry_grok.grok_profile_snapshot(profile, 3)
    exclude_cwds = tuple((c for c in exclude_cwds or () if c))
    now = _config.time.time()
    chosen = {}
    for s in snap.get("sessions") or []:
        cwd = s.get("cwd") or ""
        if (
            s.get("parent_id")
            or not s.get("active")
            or cwd.startswith(_config.STATE_DIR)
            or (exclude_cwds and cwd.startswith(exclude_cwds))
            or (_config.os.sep + "worktrees" + _config.os.sep in cwd)
        ):
            continue
        chosen[s.get("id")] = s
    for orch in _orchestrators.orchestrators_on_account(profile, "grok"):
        candidates = [
            s
            for s in snap.get("sessions") or []
            if not s.get("parent_id")
            and (not orch.get("cwd") or s.get("cwd") == orch.get("cwd"))
        ]
        if candidates:
            match = max(candidates, key=lambda s: s.get("last_active", 0))
            chosen[match.get("id")] = dict(match, active=True, orchestrator=True)
        else:
            sid = str(orch.get("session") or "orch-grok")
            chosen[sid] = {
                "id": sid,
                "cwd": orch.get("cwd"),
                "title": "Grok orchestrator",
                "last_active": orch.get("last_seen"),
                "model": orch.get("model") or _models.GROK_MODEL,
                "orchestrator": True,
                "tokens": {},
            }
    return [
        {
            "session": s.get("id"),
            "cwd": s.get("cwd"),
            "branch": s.get("branch"),
            "slug": None,
            "label": s.get("title"),
            "model": s.get("model"),
            "age_s": max(0, int(now - (s.get("last_active") or now))),
            "tok": {
                "in": (s.get("tokens") or {}).get("in", 0),
                "out": (s.get("tokens") or {}).get("out", 0),
                "cache": (s.get("tokens") or {}).get("cw", 0)
                + (s.get("tokens") or {}).get("cr", 0),
            },
            "orchestrator": bool(s.get("orchestrator")),
        }
        for s in chosen.values()
    ]


def grok_agent_details(profile, exclude_cwds=None, snapshot=None):
    from . import account_state as _account_state
    from . import config as _config
    from . import telemetry_grok as _telemetry_grok

    snap = snapshot or _telemetry_grok.grok_profile_snapshot(profile, 3)
    exclude_cwds = tuple((c for c in exclude_cwds or () if c))
    now = _config.time.time()
    children = {
        s.get("id"): s for s in snap.get("sessions") or [] if s.get("parent_id")
    }
    out = []
    for s in snap.get("sessions") or []:
        if s.get("parent_id"):
            continue
        cwd = s.get("cwd") or ""
        worker = (
            cwd.startswith(_config.STATE_DIR)
            or (exclude_cwds and cwd.startswith(exclude_cwds))
            or _config.os.sep + "worktrees" + _config.os.sep in cwd
        )
        for a in s.get("agents") or []:
            child = children.get(a.get("session")) or {}
            tok = child.get("tokens") or {}
            active = (
                a.get("status") == "running"
                and _config.time.time() - (a.get("last_active") or 0)
                <= _account_state.AGENT_ACTIVE_WINDOW_S
            )
            out.append(
                {
                    "session": s.get("id"),
                    "agent": a.get("id"),
                    "slug": None,
                    "cwd": cwd,
                    "branch": s.get("branch"),
                    "label": a.get("label") or "Grok subagent",
                    "age_s": max(0, int(now - (a.get("last_active") or now))),
                    "wf": None,
                    "working": active,
                    "done": not active,
                    "worker": worker,
                    "model": a.get("model") or child.get("model"),
                    "files": child.get("files") or [],
                    "tok": {
                        "in": tok.get("in", 0),
                        "out": tok.get("out", 0),
                        "cache": tok.get("cw", 0) + tok.get("cr", 0),
                    },
                }
            )
    return out
