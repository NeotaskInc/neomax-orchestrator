"""Armed rotation state and account cooldowns."""

from . import config as _config
from . import solo as _solo

ACCOUNT_COOLDOWN_FILE = _config.os.path.join(_config.STATE_DIR, "account-cooldown.json")
ARMED_ROTATE_FILE = _config.os.path.join(_config.STATE_DIR, "armed-rotate.json")
ARMED_ROTATE_AGE_S = 12 * 3600


def _armed_rotate_map():
    from . import config as _config

    try:
        with open(ARMED_ROTATE_FILE) as f:
            return _config.json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _write_armed_rotate(d):
    from . import config as _config

    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    try:
        tmp = "%s.tmp.%d" % (ARMED_ROTATE_FILE, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(d, f)
        _config.os.replace(tmp, ARMED_ROTATE_FILE)
    except OSError:
        pass


def set_armed_rotate(
    profile, threshold, prefer, weekly_threshold=_solo.SOLO_7D_ROTATE, auto=False
):
    """Write/refresh the armed-rotate marker for `profile`. `auto` marks a marker the system armed
    itself (an orchestrator session) vs one a human `/rotate --arm` set — purely informational. An
    AUTO marker never overwrites a human one's explicit threshold/prefer (don't clobber an operator
    directive); a human arm always wins."""
    from . import config as _config

    d = _armed_rotate_map()
    ap = _config.os.path.abspath(profile)
    existing = d.get(ap)
    if auto and existing and (not existing.get("auto", False)):
        return
    d[ap] = {
        "threshold": threshold,
        "weekly_threshold": weekly_threshold,
        "prefer": prefer or [],
        "auto": bool(auto),
        "session": (existing or {}).get("session"),
        "ts": int(_config.time.time()),
    }
    _write_armed_rotate(d)


def clear_armed_rotate(profile):
    from . import config as _config

    d = _armed_rotate_map()
    if d.pop(_config.os.path.abspath(profile), None) is not None:
        _write_armed_rotate(d)


def armed_rotate_take(profile, session_id):
    """If `profile` has an ACTIONABLE armed-rotate marker for this session — unclaimed, OR already
    claimed by `session_id`, and not aged out — CLAIM + refresh it and return {threshold, prefer}.
    Else None. The claim is what stops a leftover marker from rotating an UNRELATED later session."""
    from . import config as _config
    from . import solo as _solo

    ap = _config.os.path.abspath(profile)
    d = _armed_rotate_map()
    rec = d.get(ap)
    if not rec or _config.time.time() - rec.get("ts", 0) > ARMED_ROTATE_AGE_S:
        return None
    owner = rec.get("session")
    if owner and session_id and (owner != session_id):
        return None
    rec["session"] = session_id or owner
    rec["ts"] = int(_config.time.time())
    d[ap] = rec
    _write_armed_rotate(d)
    return {
        "threshold": rec.get("threshold", _solo.SOLO_5H_ROTATE),
        "weekly_threshold": rec.get("weekly_threshold", _solo.SOLO_7D_ROTATE),
        "prefer": rec.get("prefer") or None,
    }


def _account_cooldowns():
    from . import config as _config

    try:
        with open(ACCOUNT_COOLDOWN_FILE) as f:
            return _config.json.load(f)
    except (OSError, ValueError):
        return {}


def _account_cooled(uuid):
    from . import config as _config

    return bool(uuid) and _account_cooldowns().get(uuid, 0) > _config.time.time()


def _cool_account(uuid, until):
    from . import config as _config

    if not uuid:
        return
    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    d = _account_cooldowns()
    d[uuid] = int(until)
    try:
        tmp = "%s.tmp.%d" % (ACCOUNT_COOLDOWN_FILE, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(d, f)
        _config.os.replace(tmp, ACCOUNT_COOLDOWN_FILE)
    except OSError:
        pass


def _current_session_profile():
    from . import config as _config
    from . import models as _models

    cfg = _config.os.environ.get("CLAUDE_CONFIG_DIR")
    return (
        _config.os.path.abspath(cfg)
        if cfg
        else _config.os.path.abspath(_models.ENGINES["claude"]["default_dir"])
    )
