"""Normalized provider usage recording."""


def record_usage(rec, engine):
    """Refresh this account's 5h/7d usage cache after a run, so the NEXT
    auto-selection can avoid a near-maxed account. Claude: per-account OAuth usage
    API (fetch_claude_usage caches itself). Codex: rollout-derived window persisted
    to the unified cache. Best-effort."""
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import usage_windows as _usage_windows

    try:
        if engine in ("opencode", "kimi", "grok"):
            return
        if engine == "claude":
            _usage_windows.fetch_claude_usage(rec["profile"])
            return
        _config.os.makedirs(_config.USAGE_DIR, exist_ok=True)
        window = _codex_usage.read_codex_window(rec["profile"], rec.get("session"))
        if not window:
            return
        if (window.get("five_hour") or {}).get("used_percent") is None:
            return
        f = _config.os.path.join(
            _config.USAGE_DIR,
            "%s-%s.json" % (engine, _config.os.path.basename(rec["profile"])),
        )
        window["observed_at"] = int(_config.time.time())
        tmp = f + ".tmp"
        with open(tmp, "w") as fh:
            _config.json.dump(window, fh)
        _config.os.rename(tmp, f)
    except (OSError, ValueError):
        pass
