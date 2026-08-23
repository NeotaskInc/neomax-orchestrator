"""Concurrent orchestrator coordination and liveness."""

from . import config as _config

ORCH_DIR = _config.os.path.join(_config.STATE_DIR, "orchestrators")
ORCH_GC_AGE_S = 24 * 3600


def _orch_path(session):
    from . import config as _config

    return _config.os.path.join(
        ORCH_DIR, _config.re.sub("[^A-Za-z0-9._-]", "_", str(session)) + ".json"
    )


def register_orchestrator(session, pid, engine, reserved=False):
    """Record/refresh THIS orchestrator session (called by any provider launcher right
    before it exec's claude/codex). Captures engine, account, project + branch namespace
    (from cwd), pid (the orchestrator process — exec keeps the launcher's pid), started, and
    last_seen. Idempotent: re-registering preserves `started`."""
    from . import config as _config
    from . import handoff as _handoff
    from . import models as _models
    from . import projects as _projects
    from . import status_helpers as _status_helpers

    if not session:
        return
    _config.os.makedirs(ORCH_DIR, exist_ok=True)
    now = int(_config.time.time())
    cwd = _config.os.getcwd()
    launch_root = _config.os.environ.get("NEOMAX_PROJECT_ROOT") or cwd
    _projects.ensure_launch_project(launch_root)
    proj = _projects.project_of(launch_root) or _projects.project_of(cwd)
    pcfg = _projects.load_projects().get(proj) or {} if proj else {}
    prefix = pcfg.get("branch_prefix", (proj or "")[:3]) if proj else None
    prof = _handoff.current_orchestrator_profile(engine)
    path = _orch_path(session)
    started = now
    try:
        started = _config.json.load(open(path)).get("started", now)
    except (OSError, ValueError):
        pass
    rec = {
        "session": session,
        "pid": int(pid) if pid else None,
        "engine": engine,
        "account": _status_helpers.profile_acct_no(prof, engine),
        "account_dir": _config.os.path.basename(prof),
        "project": proj,
        "branch_prefix": prefix,
        "cwd": cwd,
        "model": _models.default_model(engine),
        "reserved": bool(reserved),
        "started": started,
        "last_seen": now,
    }
    try:
        tmp = "%s.tmp.%d" % (path, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(rec, f)
        _config.os.replace(tmp, path)
    except OSError:
        pass


def orchestrator_heartbeat(session):
    """Refresh last_seen for a live orchestrator (called from the Stop hook each turn)."""
    from . import config as _config

    if not session:
        return
    path = _orch_path(session)
    try:
        with open(path) as f:
            rec = _config.json.load(f)
    except (OSError, ValueError):
        return
    rec["last_seen"] = int(_config.time.time())
    try:
        tmp = "%s.tmp.%d" % (path, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(rec, f)
        _config.os.replace(tmp, path)
    except OSError:
        pass


def unregister_orchestrator(session):
    from . import config as _config

    if session:
        try:
            _config.os.remove(_orch_path(session))
        except OSError:
            pass


def all_orchestrators():
    """Every registered orchestrator with a live flag (pid still running). Lazily GCs
    long-dead entries. Never raises on a malformed record."""
    from . import config as _config
    from . import runs as _runs

    out = []
    if not _config.os.path.isdir(ORCH_DIR):
        return out
    now = _config.time.time()
    for f in sorted(_config.globmod.glob(_config.os.path.join(ORCH_DIR, "*.json"))):
        try:
            with open(f) as fh:
                rec = _config.json.load(fh)
        except (OSError, ValueError):
            continue
        rec["live"] = _runs.pid_alive(rec.get("pid"))
        if not rec["live"] and now - rec.get("last_seen", 0) > ORCH_GC_AGE_S:
            try:
                _config.os.remove(f)
            except OSError:
                pass
            continue
        out.append(rec)
    return out


def live_orchestrators():
    return [o for o in all_orchestrators() if o.get("live")]


def orchestrators_on_account(profile, engine="claude", exclude_session=None):
    """Live orchestrators of `engine` currently registered on the account (config-dir)
    `profile`, excluding `exclude_session`. Two interactive orchestrators on ONE config-dir
    SHARE Claude Code's per-account state — notably the native `/goal` slot, which then
    cross-contaminates (a `/goal` set in either session overwrites the other's, so the Stop
    hook starts demanding the WRONG session's goal). Used to (a) steer a launch away from an
    occupied account and (b) warn a session that landed on a shared one. Matches on the stored
    basename (`account_dir`) so it's path-root-agnostic (works under test profiles too)."""
    from . import config as _config

    want = _config.os.path.basename(_config.os.path.abspath(profile))
    out = []
    for o in live_orchestrators():
        if o.get("engine", "claude") != engine:
            continue
        if exclude_session and o.get("session") == exclude_session:
            continue
        if o.get("account_dir") == want:
            out.append(o)
    return out


def colocation_banner(engine="claude"):
    """A prominent SessionStart/opener warning if THIS orchestrator session shares its account
    with another live orchestrator (see orchestrators_on_account). '' when not co-located.
    Never raises — a degraded registry must not break the session opener."""
    from . import config as _config
    from . import handoff as _handoff

    try:
        others = orchestrators_on_account(
            _handoff.current_orchestrator_profile(engine),
            engine,
            exclude_session=current_orch_session(),
        )
    except Exception:
        return ""
    if not others:
        return ""
    who = "; ".join(
        (
            "acct %s · %s"
            % (
                o.get("account"),
                o.get("project") or _config.os.path.basename(o.get("cwd") or "?"),
            )
            for o in others
        )
    )
    if engine == "claude":
        return (
            "⚠️ SHARED-ACCOUNT WARNING — this orchestrator is on the SAME Claude account as %d other live orchestrator session(s): %s. Sessions on one account SHARE Claude Code's per-account state, including the native `/goal` slot. Relaunch on a free account.\n\n"
            % (len(others), who)
        )
    return (
        "⚠️ SHARED-ACCOUNT WARNING — this orchestrator shares %s account quota/state with %d other live orchestrator session(s): %s. Prefer another account.\n\n"
        % (engine, len(others), who)
    )


def current_orch_session():
    """This session's orchestrator id, if it IS an interactive orchestrator (the launcher
    exported NEOMAX_ORCH_SESSION). None for a plain neomax call or a worker — so ownership
    scoping only kicks in for real orchestrator sessions and never changes bare-CLI behavior."""
    from . import config as _config

    return _config.os.environ.get("NEOMAX_ORCH_SESSION") or None


def run_owner(rec):
    return rec.get("orch_session")


def owned_by_other_live_orch(rec):
    """True if this run belongs to a DIFFERENT, still-live orchestrator session — the signal
    that a destructive command (ack/clean/kill/resume/retry) should refuse without --any. Only
    applies when the CALLER is itself an orchestrator (so two orchestrators protect each
    other); a bare CLI call (no NEOMAX_ORCH_SESSION) is never scoped — unchanged behavior."""
    me = current_orch_session()
    if not me:
        return False
    owner = run_owner(rec)
    if not owner or owner == me:
        return False
    return any(
        (o.get("session") == owner and o.get("live") for o in all_orchestrators())
    )


def _proc_is_engine(cmd_lower, engine):
    from . import models as _models

    procname = _models.ENGINES.get(engine, _models.ENGINES["claude"])["procname"]
    return (
        procname in cmd_lower
        or "claude" in cmd_lower
        or "codex" in cmd_lower
        or ("opencode" in cmd_lower)
        or ("kimi" in cmd_lower)
    )


def _group_has_engine(pgid, engine):
    """Does the worker's process GROUP still contain a live engine process? Used when the
    group LEADER (worker_pid) has exited but work may continue — Codex's `codex exec` node
    wrapper can exit while its native child keeps running in the same group. Without this a
    live neomax-dispatched Codex worker reads as dead and vanishes from the dashboard."""
    from . import config as _config

    try:
        out = _config.subprocess.run(
            ["ps", "-axo", "pgid=,command="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, _config.subprocess.TimeoutExpired):
        return True
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pg = int(parts[0])
        except ValueError:
            continue
        if pg == pgid and _proc_is_engine(parts[1].lower(), engine):
            return True
    return False


def worker_alive(rec):
    """Is this run's headless worker still running, even if neomax died? Workers are spawned
    start_new_session=True, so worker_pid is the PROCESS-GROUP leader. We check the GROUP, not
    just the leader pid: a Codex worker whose node wrapper exited but whose native child keeps
    working still reads as alive (it was vanishing from the codex account card)."""
    from . import config as _config
    from . import runs as _runs

    wp = rec.get("worker_pid")
    if not wp:
        return False
    engine = rec.get("engine", "claude")
    if _runs.pid_alive(wp):
        try:
            out = _config.subprocess.run(
                ["ps", "-o", "command=", "-p", str(wp)],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.lower()
        except (OSError, _config.subprocess.TimeoutExpired):
            return True
        return _proc_is_engine(out, engine)
    return _group_has_engine(wp, engine)


TERMINAL_STATUSES = (
    "done",
    "error",
    "limit",
    "stalled",
    "timeout",
    "aborted",
    "interrupted",
)


def is_acknowledged(rec):
    """Has the orchestrator received this run's result? Runs created BEFORE the
    completion-inbox feature (#17) have no 'acknowledged' key — treat those legacy
    runs as acknowledged so an upgrade doesn't flood the inbox with old history."""
    return "acknowledged" not in rec or bool(rec.get("acknowledged"))


def in_inbox(rec):
    """A finished run the orchestrator hasn't acknowledged yet."""
    return effective_status(rec) in TERMINAL_STATUSES and (not is_acknowledged(rec))


def effective_status(rec):
    from . import runs as _runs

    status = rec.get("status")
    if not status:
        return "unknown"
    if status == "running" and (not _runs.pid_alive(rec.get("pid"))):
        if worker_alive(rec):
            return "orphaned"
        return "interrupted"
    return status
