"""Usage-aware worker account selection."""


def _reset_eta(epoch):
    """Compact 'time until' for rotate/handoff messages: '~2d' / '~5h' / '' when unknown/past."""
    from . import config as _config

    if not epoch:
        return ""
    secs = epoch - _config.time.time()
    if secs <= 0:
        return ""
    return (
        "~%dd" % round(secs / 86400)
        if secs >= 86400
        else "~%dh" % max(1, round(secs / 3600))
    )


def pick_account(selector, exclude=(), engine="claude", bias=None):
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import models as _models
    from . import quota_deadlines as _quota_deadlines
    from . import status_helpers as _status_helpers
    from . import usage_windows as _usage_windows

    plist = _models.worker_profiles(engine)
    if selector != "auto":
        if str(selector).lower() == "orch":
            p = _models.orch_profile(engine)
            if not p:
                _models.err(
                    "neomax: no dedicated orchestrator account is set up for %s; use its account helper (`cmax orchestrator` or `cdx`/`ocx`/`kmx`/`gmx orch`)"
                    % engine
                )
                _config.sys.exit(2)
            if _models.orch_reserved():
                _models.err(
                    "neomax: the orchestrator account is RESERVED in this session (--orchestrator) — workers may not run on it"
                )
                _config.sys.exit(2)
        else:
            numbered = [q for q in plist if q != _models.orch_profile(engine)]
            idx = int(selector) - 1
            if idx < 0 or idx >= len(numbered):
                _models.err("neomax: no %s account %s" % (engine, selector))
                _config.sys.exit(2)
            p = numbered[idx]
        if not _account_auth.logged_in(p, engine):
            _models.err(
                "neomax: %s account %s (%s) is not logged in" % (engine, selector, p)
            )
            _config.sys.exit(1)
        return p
    now = _config.time.time()
    counts = _account_state.live_counts(engine)

    def gather(weekly_cap, quiet=False):
        cands = []
        for p in plist:
            if p in exclude or not _account_auth.logged_in(p, engine):
                continue
            if _account_state.is_paused(p):
                if not quiet:
                    _models.err(
                        "neomax: skipping %s (PAUSED — `neomax unpause %s --engine %s` to re-enable)"
                        % (p, _status_helpers.profile_acct_no(p, engine), engine)
                    )
                continue
            if counts.get(p, 0) >= _usage_windows.LIVE_CONCURRENCY_CAP:
                if not quiet:
                    _models.err(
                        "neomax: skipping %s (%d live sessions ≥ concurrency cap %d — let it drain)"
                        % (p, counts.get(p, 0), _usage_windows.LIVE_CONCURRENCY_CAP)
                    )
                continue
            until = _account_state.cooling_down(p)
            if until:
                if not quiet:
                    _models.err(
                        "neomax: skipping %s (usage-limit cooldown, resets in %dm)"
                        % (p, (until - now) / 60)
                    )
                continue
            if _usage_windows.usage_window(p, engine) >= _usage_windows.FIVE_H_SKIP:
                if not quiet:
                    _models.err(
                        "neomax: skipping %s (5h usage window ~%.0f%% full)"
                        % (p, _usage_windows.usage_window(p, engine))
                    )
                continue
            if _usage_windows.weekly_window(p, engine) >= weekly_cap:
                if not quiet:
                    _models.err(
                        "neomax: skipping %s (WEEKLY ~%.0f%% ≥ soft gate %.0f%%)"
                        % (p, _usage_windows.weekly_window(p, engine), weekly_cap)
                    )
                continue
            cands.append(p)
        return cands

    candidates = gather(_usage_windows.SEVEN_D_SKIP)
    if not candidates:
        candidates = gather(_usage_windows.SEVEN_D_HARD, quiet=True)
        if candidates:
            _models.err(
                "neomax: every %s account is past the %.0f%% weekly soft gate — running the freshest until %.0f%% (the band is still capacity)"
                % (engine, _usage_windows.SEVEN_D_SKIP, _usage_windows.SEVEN_D_HARD)
            )
    if not candidates:
        candidates = [
            p
            for p in plist
            if p not in exclude
            and _account_auth.logged_in(p, engine)
            and (not _account_state.is_paused(p))
            and (_usage_windows.weekly_window(p, engine) < _usage_windows.SEVEN_D_HARD)
            and (
                not _usage_windows.engine_has_5h(engine)
                or _usage_windows.usage_window(p, engine) < _usage_windows.FIVE_H_HARD
            )
        ]
    if not candidates:
        _models.err(
            "neomax: no %s account with weekly headroom (<%.0f%%) — the pool is EXHAUSTED (or none logged in). In-flight work continues cross-engine; new starts must wait for the weekly resets."
            % (engine, _usage_windows.SEVEN_D_HARD)
        )
        _config.sys.exit(1)
    bias = bias or {}
    return min(
        candidates,
        key=lambda p: (
            _usage_windows.usage_window(p, engine)
            + _usage_windows.weekly_window(p, engine)
            + counts.get(p, 0) * _usage_windows.LIVE_SPREAD_WEIGHT
            + bias.get(p, 0)
            + _quota_deadlines.weekly_deadline_tier(p, engine, now)
            * _quota_deadlines.WEEKLY_TIEBREAK_WEIGHT
        ),
    )
