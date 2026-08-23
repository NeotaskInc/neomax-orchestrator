"""Universal provider session rotation."""

from . import solo as _solo


def session_rotate(
    profile=None,
    threshold=_solo.SOLO_5H_ROTATE,
    prefer=None,
    force=False,
    dry_run=False,
    weekly_threshold=_solo.SOLO_7D_ROTATE,
):
    """Rotate THIS session's profile in place to another Claude account (no /login) — by SWAPPING
    the two accounts' credentials so they trade PLACES, keeping exactly one profile per account
    (no duplicate identities).
       prefer : ordered list of account selectors (N / 'orch' / path) to rotate TO. With no
                prefer, the target is the freshest OTHER account (lowest 5h). With prefer, the
                first usable requested account wins — e.g. deliberately land on a high-weekly
                account to burn its remaining weekly allowance before it resets.
       force  : a MANUAL `/rotate` invocation — switch NOW, don't wait for the threshold. With no
                target it moves to the most-available account immediately (and says "already on the
                freshest" if this account already is). With a target it lands there. The current
                account is only COOLED if it's actually at/over threshold (spent); a forced move of
                a still-fresh account leaves it usable in its new slot. The armed monitor runs
                force=False, so it only swaps at the 5h `threshold` OR `weekly_threshold` (no churn).
    Returns a status string."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import auth_rotation as _auth_rotation
    from . import config as _config
    from . import credentials as _credentials
    from . import models as _models
    from . import quota_deadlines as _quota_deadlines
    from . import rotation_commands as _rotation_commands
    from . import rotation_state as _rotation_state
    from . import selection as _selection
    from . import selection_claims as _selection_claims
    from . import solo as _solo
    from . import usage_windows as _usage_windows

    profile = profile or _rotation_state._current_session_profile()
    if not _account_auth.logged_in(profile, "claude"):
        return "current profile (%s) not logged in" % _config.os.path.basename(profile)
    cur_5h = _usage_windows.usage_window(profile, "claude")
    cur_7d = _usage_windows.weekly_window(profile, "claude")
    cur_oa = _credentials._read_oauth_account(profile)
    cur_uuid = cur_oa.get("accountUuid")
    cur_email = cur_oa.get("emailAddress") or "?"
    over = cur_5h >= threshold or cur_7d >= weekly_threshold
    if not over and (not force):
        return (
            "current %s at %.0f%% 5h / %.0f%% 7d (< %.0f%% / %.0f%%) — no rotation yet"
            % (cur_email, cur_5h, cur_7d, threshold, weekly_threshold)
        )
    maxed = over
    win = _usage_windows.fetch_claude_usage(profile) or {}
    reset = (win.get("five_hour") or {}).get("resets_at") or _config.time.time() + 3600
    cands = []
    for p in _models.engine_profiles("claude"):
        if (
            _config.os.path.abspath(p) == _config.os.path.abspath(profile)
            or not _account_auth.logged_in(p, "claude")
            or _account_state.is_paused(p)
        ):
            continue
        uuid = _credentials._read_oauth_account(p).get("accountUuid")
        if not uuid or uuid == cur_uuid or _rotation_state._account_cooled(uuid):
            continue
        if _usage_windows.weekly_window(p, "claude") >= _usage_windows.SEVEN_D_HARD:
            continue
        cands.append((p, _usage_windows.usage_window(p, "claude")))
    best = best_5h = None
    pref_paths = [
        _config.os.path.abspath(pp)
        for pp in (_credentials._resolve_profile(s, "claude") for s in prefer or [])
        if pp
    ]
    if pref_paths:
        by_path = {_config.os.path.abspath(p): (p, w) for (p, w) in cands}
        for pp in pref_paths:
            if pp in by_path:
                (best, best_5h) = by_path[pp]
                break
        if best is None and force:
            for pp in pref_paths:
                if (
                    pp == _config.os.path.abspath(profile)
                    or not _account_auth.logged_in(pp, "claude")
                    or _account_state.is_paused(pp)
                ):
                    continue
                u = _credentials._read_oauth_account(pp).get("accountUuid")
                if (
                    not u
                    or u == cur_uuid
                    or _usage_windows.weekly_window(pp, "claude")
                    >= _usage_windows.SEVEN_D_HARD
                ):
                    continue
                (best, best_5h) = (pp, _usage_windows.usage_window(pp, "claude"))
                break
        if best is None and force:
            names = ", ".join(
                (
                    _config.os.path.basename(
                        _credentials._resolve_profile(s, "claude") or str(s)
                    )
                    for s in prefer
                )
            )
            return (
                "requested account(s) [%s] can't be rotated to (not logged in / already current / paused / weekly-exhausted) — no rotation"
                % names
            )
    if best is None:
        if not cands:
            return (
                "account %s hit %.0f%% but NO other Claude account is available (all cooled/paused/maxed) — waiting for a 5h reset"
                % (cur_email, cur_5h)
            )
        by_path = {_config.os.path.abspath(p): (p, w) for (p, w) in cands}
        counts = _account_state.live_counts("claude")
        idle = [
            p for (p, w) in cands if not _rotation_commands._profile_recently_active(p)
        ]
        pool = idle or [p for (p, w) in cands]
        chosen = _selection_claims.pick_least_loaded(pool, "claude")
        (best, best_5h) = by_path[_config.os.path.abspath(chosen)]
        cur_rank = _selection_claims.rotation_rank(
            profile, "claude", counts=counts, include_claims=False
        )
        if (
            force
            and (not maxed)
            and (
                _selection_claims.rotation_rank(
                    best, "claude", counts=counts, include_claims=False
                )
                >= cur_rank
            )
        ):
            busy_better = [
                (p, w)
                for (p, w) in cands
                if _selection_claims.rotation_rank(
                    p, "claude", counts=counts, include_claims=False
                )
                < cur_rank
                and _rotation_commands._profile_recently_active(p)
            ]
            if busy_better:
                names = ", ".join(
                    (
                        "%s (%.0f%% 5h)"
                        % (
                            _credentials._read_oauth_account(p).get("emailAddress")
                            or _config.os.path.basename(p),
                            w,
                        )
                        for (p, w) in busy_better
                    )
                )
                return (
                    "the most-available accounts are BUSY with live workers [%s] — swapping with one would pull its account out from under a running worker, so I left you put. You're on the best available IDLE account (%s, %.0f%% 5h). To force onto a specific account anyway, run `/rotate <N>` (e.g. /rotate 6). Auto-rotation stays armed for 99%%."
                    % (names, cur_email, cur_5h)
                )
            return (
                "already on the most available account — %s (%.0f%% 5h) is the best target among every available account (soonest weekly reset, then freshest 5h), so there's nothing to rotate to. Auto-rotation is armed; it'll swap automatically at %.0f%%."
                % (cur_email, cur_5h, threshold)
            )
    new_email_preview = _credentials._read_oauth_account(best).get(
        "emailAddress"
    ) or _config.os.path.basename(best)
    if dry_run:
        return (
            "DRY-RUN: would rotate this session %s (%.0f%% 5h) → %s (%.0f%% 5h)%s — no swap performed."
            % (
                cur_email,
                cur_5h,
                new_email_preview,
                best_5h,
                " [%s]" % _config.os.path.basename(best) if not pref_paths else "",
            )
        )
    try:
        _config.os.makedirs(_credentials.AUTH_BACKUPS, exist_ok=True)
        _config.os.chmod(_credentials.AUTH_BACKUPS, 448)
        ts = int(_config.time.time())
        _auth_rotation._dump_private(
            _config.os.path.join(
                _credentials.AUTH_BACKUPS,
                "%d-%s.json" % (ts, _config.os.path.basename(profile)),
            ),
            {
                "engine": "claude",
                "blob": _credentials.read_claude_cred_blob(profile) or "",
                "oauth_account": cur_oa,
                "ts": ts,
            },
        )
        _auth_rotation._dump_private(
            _config.os.path.join(
                _credentials.AUTH_BACKUPS,
                "%d-%s.json" % (ts, _config.os.path.basename(best)),
            ),
            {
                "engine": "claude",
                "blob": _credentials.read_claude_cred_blob(best) or "",
                "oauth_account": _credentials._read_oauth_account(best),
                "ts": ts,
            },
        )
    except OSError:
        pass
    new_email = _credentials._read_oauth_account(best).get(
        "emailAddress"
    ) or _config.os.path.basename(best)
    best_7d = _usage_windows.weekly_window(best, "claude")
    best_reset = _selection._reset_eta(_quota_deadlines.weekly_reset_at(best, "claude"))
    if not _solo._swap_claude_auth(profile, best):
        return "FAILED to swap credentials with %s" % _config.os.path.basename(best)
    _account_state.clear_cooldown(profile)
    if maxed:
        _rotation_state._cool_account(cur_uuid, reset)
        _account_state.set_cooldown(best, reset)
    else:
        _account_state.clear_cooldown(best)
    _credentials._log_auth_rotation(
        {
            "ts": int(_config.time.time()),
            "engine": "claude",
            "dest": _config.os.path.basename(profile),
            "src": _config.os.path.basename(best),
            "from_email": cur_email,
            "to_email": new_email,
            "reason": "/rotate%s SWAP 5h %.0f%%%s"
            % (
                " TO " + _config.os.path.basename(best) if pref_paths else "",
                cur_5h,
                ">=%.0f%%" % threshold if maxed else " (manual)",
            ),
        }
    )
    if maxed:
        return (
            "ROTATED (swapped places) — %s (%.0f%% 5h) ⇄ %s (%.0f%% 5h): this session now runs %s; the spent %s moved into %s's slot to cool until its 5h reset. Adopts at next token refresh (no /login)."
            % (
                cur_email,
                cur_5h,
                new_email,
                best_5h,
                new_email,
                cur_email,
                _config.os.path.basename(best),
            )
        )
    return (
        "ROTATED (swapped places) — now running %s (%.0f%% 5h, %.0f%% 7d%s); %s (%.0f%% 5h, still has headroom) moved into %s's slot. Adopts at next token refresh (no /login)."
        % (
            new_email,
            best_5h,
            best_7d,
            ", weekly resets %s" % best_reset if best_reset else "",
            cur_email,
            cur_5h,
            _config.os.path.basename(best),
        )
    )


_NUMWORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
