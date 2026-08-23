"""Weekly quota deadline and reset policy."""

from . import config as _config

ROTATE_5H_CEILING = float(_config.os.environ.get("NEOMAX_ROTATE_5H_CEILING", "95"))
WEEKLY_BUCKET_S = float(
    _config.os.environ.get("NEOMAX_WEEKLY_BUCKET_S", str(24 * 3600))
)
WEEKLY_HORIZON_S = 8 * 24 * 3600
WEEKLY_TIEBREAK_WEIGHT = float(_config.os.environ.get("NEOMAX_WEEKLY_TIEBREAK", "1.5"))


def weekly_reset_at(profile, engine):
    """Epoch when this account's 7-day window resets; None if unknown or already reset. Reads the
    on-disk usage cache DIRECTLY (cheap, read-only — selection must never trigger a network/keychain
    refresh; usage_window + the usage-watcher keep the cache warm). Never raises."""
    from . import account_state as _account_state
    from . import config as _config

    base = _config.os.path.basename(profile)
    if engine not in ("claude", "codex"):
        return None
    cache = _config.os.path.join(
        _config.USAGE_DIR,
        ("claude-%s.json" if engine == "claude" else "codex-%s.json") % base,
    )
    try:
        with open(cache) as f:
            d = _config.json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("expired"):
        return None
    resets = _account_state.to_epoch_seconds(
        (d.get("seven_day") or {}).get("resets_at")
    )
    if not resets or resets <= _config.time.time():
        return None
    return resets


def weekly_deadline_tier(profile, engine, now=None):
    """Whole WEEKLY_BUCKET_S periods until this account's weekly resets (0 = resets within the next
    bucket = MOST use-it-or-lose-it pressure). Accounts resetting in the SAME bucket are 'in the same
    area' -> ranked by 5h instead. Unknown/no-reset -> a far tier (no deadline pressure)."""
    from . import config as _config

    reset = weekly_reset_at(profile, engine)
    if not reset:
        return int(WEEKLY_HORIZON_S / WEEKLY_BUCKET_S)
    now = now if now is not None else _config.time.time()
    return int(max(0.0, reset - now) / WEEKLY_BUCKET_S)


ROTATE_CLAIM_TTL_S = float(_config.os.environ.get("NEOMAX_ROTATE_CLAIM_TTL", "120"))
