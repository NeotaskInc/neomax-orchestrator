"""Single-account Claude mode and in-place failover."""

SOLO_5H_ROTATE = 99.0
SOLO_7D_ROTATE = 99.0


def _solo_profile():
    from . import config as _config

    return _config.os.path.join(_config.HOME, ".claude-solo")


def _native_claude_accounts():
    """Real Claude account profiles (NOT the solo profile) — measured for 5h + used as the
    rotation source. Excludes the dedicated reserved-orchestrator profile too."""
    from . import config as _config
    from . import models as _models

    solo = _config.os.path.abspath(_solo_profile())
    return [
        p
        for p in _models.engine_profiles("claude")
        if _config.os.path.abspath(p) != solo
    ]


def _copy_claude_auth(dest, src):
    """In-place credential copy src->dest (the rotate-auth core, NO /login). True on success.
    The live session on `dest` adopts the new account at its next token refresh."""
    from . import config as _config
    from . import credentials as _credentials

    blob = _credentials.read_claude_cred_blob(src)
    if not blob:
        return False
    src_oa = _credentials._read_oauth_account(src)
    _config.os.makedirs(dest, exist_ok=True)
    if not _credentials.write_claude_cred_blob(dest, blob):
        return False
    if src_oa:
        _credentials._write_oauth_account(dest, src_oa)
    for c in ("claude-%s.json", "email-%s.json"):
        try:
            _config.os.remove(
                _config.os.path.join(
                    _config.USAGE_DIR, c % _config.os.path.basename(dest)
                )
            )
        except OSError:
            pass
    return True


def _swap_claude_auth(profile_a, profile_b):
    """SWAP (exchange) the stored credentials of two REAL Claude account profiles in place — no
    /login. profile_a ends up holding profile_b's account and vice-versa, so a maxed account
    moves INTO the freshest account's slot and the fleet keeps EXACTLY ONE profile per account
    (no duplicate identities). True on success. Atomic-ish: if the second write fails we roll the
    first one back so we never leave a half-swap (two profiles on the same account)."""
    from . import config as _config
    from . import credentials as _credentials

    blob_a = _credentials.read_claude_cred_blob(profile_a)
    blob_b = _credentials.read_claude_cred_blob(profile_b)
    if not blob_a or not blob_b:
        return False
    oa_a = _credentials._read_oauth_account(profile_a)
    oa_b = _credentials._read_oauth_account(profile_b)
    _config.os.makedirs(profile_a, exist_ok=True)
    _config.os.makedirs(profile_b, exist_ok=True)
    if not _credentials.write_claude_cred_blob(profile_a, blob_b):
        return False
    if not _credentials.write_claude_cred_blob(profile_b, blob_a):
        _credentials.write_claude_cred_blob(profile_a, blob_a)
        return False
    if oa_b:
        _credentials._write_oauth_account(profile_a, oa_b)
    if oa_a:
        _credentials._write_oauth_account(profile_b, oa_a)
    for prof in (profile_a, profile_b):
        for c in ("claude-%s.json", "email-%s.json"):
            try:
                _config.os.remove(
                    _config.os.path.join(
                        _config.USAGE_DIR, c % _config.os.path.basename(prof)
                    )
                )
            except OSError:
                pass
    return True


def solo_pick_freshest(exclude_uuid=None):
    """Freshest native Claude account (lowest 5h) that is logged in, NOT paused, NOT 5h-cooled,
    NOT weekly-exhausted (>=99%), and NOT the account currently in use (exclude_uuid). None if
    none available (all cooled/paused/maxed / only the current)."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import credentials as _credentials
    from . import selection_claims as _selection_claims
    from . import usage_windows as _usage_windows

    cands = []
    for p in _native_claude_accounts():
        if (
            not _account_auth.logged_in(p, "claude")
            or _account_state.is_paused(p)
            or _account_state.cooling_down(p)
        ):
            continue
        if _usage_windows.weekly_window(p, "claude") >= _usage_windows.SEVEN_D_HARD:
            continue
        if (
            exclude_uuid
            and _credentials._read_oauth_account(p).get("accountUuid") == exclude_uuid
        ):
            continue
        cands.append(p)
    if not cands:
        return None
    return min(cands, key=lambda p: _selection_claims.rotation_rank(p, "claude"))


def solo_rotate(threshold=SOLO_5H_ROTATE, weekly_threshold=SOLO_7D_ROTATE, quiet=False):
    """If the solo session's CURRENT account is at/over its 5h OR weekly threshold, rotate its auth
    in place to the freshest other account (no /login) and cool the maxed account until the relevant
    window resets. Returns a short status string. Driven by the per-turn Stop hook (no poller).
    When the spent account's native slot and the fresh account's slot are BOTH idle, the two NATIVE
    slots are SWAPPED (the spent account trades places into the fresh account's old slot, cooled
    there) so no native slot is left duplicating another — solo then mirrors the now-fresh primary.
    If either slot has a live session, it falls back to an isolated copy (natives untouched)."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import credentials as _credentials
    from . import rotation_commands as _rotation_commands
    from . import rotation_state as _rotation_state
    from . import usage_windows as _usage_windows

    solo = _solo_profile()
    if not _config.os.path.isdir(solo) or not _account_auth.logged_in(solo, "claude"):
        return "no solo profile (run `cmax solo`)"
    cur_5h = _usage_windows.usage_window(solo, "claude")
    cur_7d = _usage_windows.weekly_window(solo, "claude")
    cur_oa = _credentials._read_oauth_account(solo)
    cur_uuid = cur_oa.get("accountUuid")
    cur_email = cur_oa.get("emailAddress") or "?"
    over_5h = cur_5h >= threshold
    over_7d = cur_7d >= weekly_threshold
    if not over_5h and (not over_7d):
        return (
            "current %s at %.0f%% 5h / %.0f%% 7d (< %.0f%% / %.0f%%) — no rotation"
            % (cur_email, cur_5h, cur_7d, threshold, weekly_threshold)
        )
    trigger = (
        "weekly %.0f%%>=%.0f%%" % (cur_7d, weekly_threshold)
        if over_7d
        else "5h %.0f%%>=%.0f%%" % (cur_5h, threshold)
    )
    cur_native = next(
        (
            p
            for p in _native_claude_accounts()
            if _credentials._read_oauth_account(p).get("accountUuid") == cur_uuid
        ),
        None,
    )
    reset = None
    if cur_native:
        win = _usage_windows.fetch_claude_usage(cur_native) or {}
        reset = (win.get("seven_day" if over_7d else "five_hour") or {}).get(
            "resets_at"
        )
    reset = reset or _config.time.time() + (7 * 24 * 3600 if over_7d else 3600)
    fresh = solo_pick_freshest(exclude_uuid=cur_uuid)
    if not fresh:
        return (
            "account %s hit its limit (%s) but NO fresher Claude account is available (all cooled/weekly-maxed) — waiting for a window to reset"
            % (cur_email, trigger)
        )
    new_email = _credentials._read_oauth_account(fresh).get(
        "emailAddress"
    ) or _config.os.path.basename(fresh)
    (fresh_5h, fresh_7d) = (
        _usage_windows.usage_window(fresh, "claude"),
        _usage_windows.weekly_window(fresh, "claude"),
    )
    swapped = False
    if (
        cur_native
        and _config.os.path.abspath(cur_native) != _config.os.path.abspath(fresh)
        and (not _rotation_commands._profile_recently_active(cur_native))
        and (not _rotation_commands._profile_recently_active(fresh))
    ):
        if not _swap_claude_auth(cur_native, fresh):
            return "FAILED to swap credentials between %s and %s" % (
                _config.os.path.basename(cur_native),
                _config.os.path.basename(fresh),
            )
        _account_state.set_cooldown(fresh, reset)
        _rotation_state._cool_account(cur_uuid, reset)
        _account_state.clear_cooldown(cur_native)
        if not _copy_claude_auth(solo, cur_native):
            return "FAILED to point solo at the fresh account"
        swapped = True
    else:
        if cur_native:
            _account_state.set_cooldown(cur_native, reset)
        if not _copy_claude_auth(solo, fresh):
            return "FAILED to copy credentials from %s" % _config.os.path.basename(
                fresh
            )
    _credentials._log_auth_rotation(
        {
            "ts": int(_config.time.time()),
            "engine": "claude",
            "dest": ".claude-solo",
            "src": _config.os.path.basename(fresh),
            "from_email": cur_email,
            "to_email": new_email,
            "reason": "solo " + trigger + (" (swap)" if swapped else " (copy)"),
        }
    )
    return (
        "ROTATED (%s%s) — %s (%.0f%% 5h, %.0f%% 7d; cooled until reset) → %s (%.0f%% 5h, %.0f%% 7d); adopts at next token refresh (no /login)"
        % (
            trigger,
            ", swapped places" if swapped else "",
            cur_email,
            cur_5h,
            cur_7d,
            new_email,
            fresh_5h,
            fresh_7d,
        )
    )


def cmd_solo_rotate(argv):
    """SOLO MODE one-shot rotation check (used by the monitor + Stop hook).
    neomax solo-rotate [--threshold N] [--quiet]"""
    threshold = SOLO_5H_ROTATE
    if "--threshold" in argv:
        try:
            threshold = float(argv[argv.index("--threshold") + 1])
        except (ValueError, IndexError):
            pass
    msg = solo_rotate(threshold=threshold, quiet="--quiet" in argv)
    if msg:
        print("solo: " + msg)


def cmd_solo_setup(argv):
    """Seed the solo profile (~/.claude-solo) with the FRESHEST native Claude account's
    credentials so `cmax solo` starts on the best account. Prints the account email/dir.
       neomax solo-setup [--account N]   (--account pins a specific account instead)"""
    from . import account_auth as _account_auth
    from . import config as _config
    from . import credentials as _credentials
    from . import models as _models

    fresh = None
    if "--account" in argv:
        try:
            fresh = _credentials._resolve_profile(
                argv[argv.index("--account") + 1], "claude"
            )
        except (IndexError, SystemExit):
            fresh = None
    if not fresh:
        fresh = solo_pick_freshest()
    if not fresh or not _account_auth.logged_in(fresh, "claude"):
        _models.err(
            "neomax solo-setup: no logged-in, un-cooled Claude account to start on"
        )
        _config.sys.exit(1)
    solo = _solo_profile()
    _config.os.makedirs(solo, exist_ok=True)
    if not _copy_claude_auth(solo, fresh):
        _models.err(
            "neomax solo-setup: failed to copy credentials from %s"
            % _config.os.path.basename(fresh)
        )
        _config.sys.exit(1)
    print(
        _credentials._read_oauth_account(fresh).get("emailAddress")
        or _config.os.path.basename(fresh)
    )
