"""Cooldowns, pauses, reset normalization, and live counts."""


def load_cooldowns():
    from . import config as _config

    try:
        with open(_config.COOLDOWN_FILE) as f:
            return _config.json.load(f)
    except (OSError, ValueError):
        return {}


def set_cooldown(profile, until_epoch):
    from . import config as _config

    now = _config.time.time()
    try:
        until = min(max(now, float(until_epoch)), now + MAX_RESET_HORIZON_S)
    except (TypeError, ValueError):
        until = now + _config.DEFAULT_COOLDOWN_S
    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(_config.COOLDOWN_FILE + ".lock", "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        cd = load_cooldowns()
        cd[profile] = int(until)
        tmp = "%s.tmp.%d" % (_config.COOLDOWN_FILE, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(cd, f)
        _config.os.replace(tmp, _config.COOLDOWN_FILE)
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass


def cooling_down(profile):
    from . import config as _config

    until = load_cooldowns().get(profile, 0)
    return until if until > _config.time.time() else 0


def clear_cooldown(profile):
    """Remove a profile's cooldown (locked read-modify-write). Used after a rotation SWAP:
    the profile that received the FRESH account must not stay cooled from a prior maxing."""
    from . import config as _config

    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(_config.COOLDOWN_FILE + ".lock", "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        cd = load_cooldowns()
        if profile in cd:
            del cd[profile]
            tmp = "%s.tmp.%d" % (_config.COOLDOWN_FILE, _config.os.getpid())
            with open(tmp, "w") as f:
                _config.json.dump(cd, f)
            _config.os.replace(tmp, _config.COOLDOWN_FILE)
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass


def load_paused():
    """Set of paused profile abspaths. A paused account is MANUALLY held out of dispatch
    (no auto-expiry, unlike cooldown) until explicitly unpaused."""
    from . import config as _config

    try:
        with open(_config.PAUSED_FILE) as f:
            d = _config.json.load(f)
        return {
            _config.os.path.abspath(p) for p in (d if isinstance(d, list) else list(d))
        }
    except (OSError, ValueError):
        return set()


def is_paused(profile):
    """True if this account is manually paused (hard-excluded from auto-dispatch). Read fresh
    from disk so a dashboard/CLI pause takes effect on the NEXT dispatch — no orchestrator
    restart needed (pick_account calls this per selection)."""
    from . import config as _config

    return _config.os.path.abspath(profile) in load_paused()


def set_paused(profile, paused=True):
    """Add/remove a profile from the paused set (flock-guarded read-modify-write, atomic)."""
    from . import config as _config

    prof = _config.os.path.abspath(profile)
    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(_config.PAUSED_FILE + ".lock", "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        cur = load_paused()
        if paused:
            cur.add(prof)
        else:
            cur.discard(prof)
        tmp = "%s.tmp.%d" % (_config.PAUSED_FILE, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(sorted(cur), f)
        _config.os.replace(tmp, _config.PAUSED_FILE)
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass


MAX_RESET_HORIZON_S = 7 * 24 * 3600 + 3600


def to_epoch_seconds(value):
    """Coerce an API reset value to epoch SECONDS (scaling ms/µs down), or None if
    non-numeric. Preserves past times (a window that already reset has a past
    resets_at — meaningful to the usage cache)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    while v > 100000000000.0:
        v /= 1000.0
    return v


def normalize_reset_epoch(value):
    """Coerce an API-reported rate-limit reset into a sane FUTURE epoch-seconds time,
    or None. The 5h window is ROLLING — the reset is whatever the API reports (often
    far less than 5h away), NEVER now+5h. Rejects missing/garbage/already-past values
    and anything past MAX_RESET_HORIZON_S so a bad field can't cool an account 'forever'."""
    from . import config as _config

    v = to_epoch_seconds(value)
    if v is None:
        return None
    now = _config.time.time()
    if v <= now or v > now + MAX_RESET_HORIZON_S:
        return None
    return v


def live_counts(engine="claude"):
    """Per-account in-flight worker load for balancing.
    - codex/opencode/kimi/grok: count running workers from OUR durable registry (exact;
      their multi-process trees make ps-counting unreliable).
    - claude: count real claude processes (catches neomax workers AND the
      user's own interactive sessions; claude is a clean single native process)."""
    from . import ambient_sessions as _ambient_sessions

    if engine in ("codex", "opencode", "kimi", "grok"):
        return registry_live_counts(engine)
    return _ambient_sessions.process_live_counts("claude")


def registry_live_counts(engine):
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    counts = {p: 0 for p in _models.engine_profiles(engine)}
    for rec in _runs.all_runs():
        if rec.get("engine", "claude") != engine:
            continue
        if (
            rec.get("profile") in counts
            and rec.get("status") == "running"
            and _orchestrators.worker_alive(rec)
        ):
            counts[rec["profile"]] += 1
    return counts


AGENT_ACTIVE_WINDOW_S = 240
WORKFLOW_RECENT_S = 900
