"""Concurrency-safe rotation claims and ranking."""


def _rotate_claim_file():
    from . import config as _config

    return _config.os.path.join(_config.STATE_DIR, "rotation-claims.json")


def _rotate_lock_file():
    from . import config as _config

    return _config.os.path.join(_config.STATE_DIR, "rotation.lock")


def _rotation_claims():
    from . import config as _config

    try:
        with open(_rotate_claim_file()) as f:
            return _config.json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _rotation_claim_count(profile, now=None):
    """1 if `profile` is currently claimed as another rotation's target (un-expired), else 0 —
    added to its spread so concurrent rotations don't converge. Pure read; never raises."""
    from . import config as _config
    from . import quota_deadlines as _quota_deadlines

    now = now if now is not None else _config.time.time()
    ts = _rotation_claims().get(_config.os.path.abspath(profile))
    return 1 if ts and now - ts < _quota_deadlines.ROTATE_CLAIM_TTL_S else 0


def _claim_rotation_target(profile):
    """Record `profile` as a just-chosen rotation target (prunes expired claims). The CALLER must
    already hold the rotation flock (pick_least_loaded does) so the write is serialized. Best-effort."""
    from . import config as _config
    from . import quota_deadlines as _quota_deadlines

    ap = _config.os.path.abspath(profile)
    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    now = _config.time.time()
    d = {
        k: v
        for (k, v) in _rotation_claims().items()
        if now - v < _quota_deadlines.ROTATE_CLAIM_TTL_S
    }
    d[ap] = now
    claim_file = _rotate_claim_file()
    try:
        tmp = "%s.tmp.%d" % (claim_file, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(d, f)
        _config.os.replace(tmp, claim_file)
    except OSError:
        pass


def rotation_rank(profile, engine, now=None, counts=None, include_claims=True):
    """Sort key (LOWER = better) for choosing ONE account to run/rotate a session onto — rotate /
    solo / orchestrator launch + handoff. 5h-FIRST (the operator's rule: 'target the one with the
    next lowest 5-hour usage'), with an anti-herd SPREAD term so concurrent rotations don't all
    land on the same account, and weekly only as a gentle nudge. Order:
      1) 5h headroom  — accounts under the 5h ceiling beat any already at/over it (never land on an
         about-to-wall account, which would just trigger another rotation);
      2) lowest 5h  +  SPREAD (live sessions already on it + any in-flight rotation claim, each
         ≈ LIVE_SPREAD_WEIGHT% of 5h)  +  a SMALL weekly-deadline nudge (soonest-resetting account
         shaves points, to burn use-it-or-lose-it quota) — so the freshest, least-contended account
         wins but two simultaneous rotations spread instead of colliding;
      3) lowest weekly — the final tiebreak.
    `counts` (per-profile live-session map) is computed ONCE by the caller and passed in to avoid a
    pgrep per candidate; falls back to a lazy fetch so older/test call sites still work.
    `include_claims=False` drops the rotation-CLAIM part of the spread — used by the 'am I already
    on the freshest account?' gate, which must judge raw freshness and NOT be fooled by the claim
    the picker just wrote on the chosen target (that claim is for OTHER racing sessions, not self)."""
    from . import account_state as _account_state
    from . import quota_deadlines as _quota_deadlines
    from . import usage_windows as _usage_windows

    if counts is None:
        counts = _account_state.live_counts(engine)
    five = _usage_windows.usage_window(profile, engine)
    over_ceiling = 1 if five >= _quota_deadlines.ROTATE_5H_CEILING else 0
    contention = counts.get(profile, 0) + (
        _rotation_claim_count(profile, now) if include_claims else 0
    )
    spread = contention * _usage_windows.LIVE_SPREAD_WEIGHT
    nudge = (
        _quota_deadlines.weekly_deadline_tier(profile, engine, now)
        * _quota_deadlines.WEEKLY_TIEBREAK_WEIGHT
    )
    return (
        over_ceiling,
        five + spread + nudge,
        _usage_windows.weekly_window(profile, engine),
    )


def pick_least_loaded(profiles, engine, now=None):
    """Choose ONE account from `profiles` (already filtered to usable candidates) for a session to
    run/rotate onto, and CLAIM it — flock-guarded so concurrent rotations across DIFFERENT sessions
    serialize and SPREAD (each picker sees the previous one's claim) instead of all converging on
    the same freshest account. 5h-first via rotation_rank. Returns the chosen profile, or None if
    `profiles` is empty. Never raises (degrades to an unlocked pick if the lock can't be taken)."""
    from . import account_state as _account_state
    from . import config as _config

    profiles = list(profiles)
    if not profiles:
        return None
    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(_rotate_lock_file(), "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        counts = _account_state.live_counts(engine)
        best = min(
            profiles, key=lambda p: rotation_rank(p, engine, now=now, counts=counts)
        )
        _claim_rotation_target(best)
        return best
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass
