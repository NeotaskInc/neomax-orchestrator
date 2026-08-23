"""Explicit credential rotation and Claude identities."""


def _dump_private(path, obj):
    """Write a credential backup as a 0600 file — these hold plaintext OAuth tokens / Codex
    auth.json; the default umask would leave them group/world-readable (the live store uses
    0600, so the backups must match). Module-level: rotate-auth + session_rotate both use it."""
    from . import config as _config

    fd = _config.os.open(
        path, _config.os.O_WRONLY | _config.os.O_CREAT | _config.os.O_TRUNC, 384
    )
    with _config.os.fdopen(fd, "w") as f:
        _config.json.dump(obj, f)


def cmd_rotate_auth(argv):
    """IN-PLACE auth rotation: overwrite a profile's credentials with ANOTHER account's
    (both already authenticated — this only COPIES existing valid tokens; it never talks
    to the OAuth server, so it can't brick anything). The profile's live session adopts
    the new account at its next token refresh (interactive windows: /login is the instant
    manual path); every NEW process under the profile uses it immediately; the dashboard
    identity flips at once (.claude.json oauthAccount is updated too). The displaced
    credentials are BACKED UP first and restorable.
       neomax rotate-auth <dest> --from <src> [--engine claude|codex] [--reason "..."]
       neomax rotate-auth <dest> --from <src> --swap                  SWAP places (exchange
                                                creds both ways — no duplicate accounts)
       neomax rotate-auth --restore <dest> [--engine ...]    restore the last backup
       neomax rotate-auth --log                              recent rotations
    <dest>/<src> = account number, 'orch', or a profile path."""
    from . import config as _config
    from . import credentials as _credentials
    from . import models as _models
    from . import solo as _solo

    engine = "claude"
    if "--engine" in argv:
        i = argv.index("--engine")
        engine = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
    if engine == "opencode" and "--log" not in argv:
        _models.err(
            "neomax: OpenCode auth profiles are isolated and never copied. Authenticate/move with `ocx login N` or `neomax handoff`."
        )
        _config.sys.exit(2)
    swap = "--swap" in argv
    argv = [a for a in argv if a != "--swap"]
    if "--log" in argv:
        for r in _credentials.recent_auth_rotations():
            print(
                "%s  %s: %s -> %s (%s)"
                % (
                    _config.time.strftime(
                        "%m-%d %H:%M", _config.time.localtime(r["ts"])
                    ),
                    r.get("engine"),
                    r.get("from_email") or r.get("dest"),
                    r.get("to_email") or r.get("src"),
                    r.get("reason") or "manual",
                )
            )
        return
    reason = None
    if "--reason" in argv:
        i = argv.index("--reason")
        reason = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
    if "--restore" in argv:
        argv = [a for a in argv if a != "--restore"]
        dest = _credentials._resolve_profile(argv[0], engine) if argv else None
        if not dest:
            _models.err("neomax: rotate-auth --restore <dest>")
            _config.sys.exit(2)
        backups = sorted(
            _config.globmod.glob(
                _config.os.path.join(
                    _credentials.AUTH_BACKUPS,
                    "*-%s.json" % _config.os.path.basename(dest),
                )
            )
        )
        if not backups:
            _models.err("neomax: no auth backup for %s" % dest)
            _config.sys.exit(1)
        with open(backups[-1]) as f:
            bk = _config.json.load(f)
        blob = bk.get("blob") or ""
        if not blob:
            _models.err("neomax: backup for %s has no credential blob" % dest)
            _config.sys.exit(1)
        if engine == "codex":
            _config.os.makedirs(dest, exist_ok=True)
            with open(_config.os.path.join(dest, "auth.json"), "w") as f:
                f.write(blob)
        else:
            _credentials.write_claude_cred_blob(dest, blob)
            if bk.get("oauth_account"):
                _credentials._write_oauth_account(dest, bk["oauth_account"])
        _credentials._log_auth_rotation(
            {
                "ts": int(_config.time.time()),
                "engine": engine,
                "dest": _config.os.path.basename(dest),
                "src": "RESTORE",
                "to_email": (bk.get("oauth_account") or {}).get("emailAddress"),
                "reason": "restore",
            }
        )
        print(
            "rotate-auth: restored %s from %s"
            % (dest, _config.os.path.basename(backups[-1]))
        )
        return
    if "--from" not in argv or not argv:
        _models.err("neomax: rotate-auth <dest> --from <src> [--engine claude|codex]")
        _config.sys.exit(2)
    i = argv.index("--from")
    src_sel = argv[i + 1]
    dest_sel = [a for a in argv[:i] + argv[i + 2 :] if not a.startswith("-")]
    dest = _credentials._resolve_profile(dest_sel[0], engine) if dest_sel else None
    src = _credentials._resolve_profile(src_sel, engine)
    if not dest or not src:
        _models.err("neomax: cannot resolve dest %r / src %r" % (dest_sel, src_sel))
        _config.sys.exit(2)
    if _config.os.path.abspath(dest) == _config.os.path.abspath(src):
        _models.err("neomax: dest and src are the same profile")
        _config.sys.exit(2)
    _config.os.makedirs(_credentials.AUTH_BACKUPS, exist_ok=True)
    try:
        _config.os.chmod(_credentials.AUTH_BACKUPS, 448)
    except OSError:
        pass
    ts = int(_config.time.time())
    if swap:
        if engine == "codex":
            (ap, bp) = (
                _config.os.path.join(dest, "auth.json"),
                _config.os.path.join(src, "auth.json"),
            )
            try:
                with open(ap) as f:
                    da = f.read()
                with open(bp) as f:
                    sa = f.read()
            except OSError:
                _models.err("neomax: both profiles must have auth.json to swap (codex)")
                _config.sys.exit(1)
            for p, blob in ((dest, da), (src, sa)):
                _dump_private(
                    _config.os.path.join(
                        _credentials.AUTH_BACKUPS,
                        "%d-%s.json" % (ts, _config.os.path.basename(p)),
                    ),
                    {"engine": "codex", "blob": blob, "ts": ts},
                )
            with open(ap, "w") as f:
                f.write(sa)
            with open(bp, "w") as f:
                f.write(da)
            print(
                "rotate-auth: SWAPPED %s ⇄ %s (codex auth exchanged). Each adopts at next use."
                % (_config.os.path.basename(dest), _config.os.path.basename(src))
            )
        else:
            (dest_oa, src_oa) = (
                _credentials._read_oauth_account(dest),
                _credentials._read_oauth_account(src),
            )
            if dest_oa.get("accountUuid") and dest_oa.get("accountUuid") == src_oa.get(
                "accountUuid"
            ):
                _models.err(
                    "neomax: dest and src are ALREADY the same account — nothing to swap"
                )
                _config.sys.exit(2)
            for p, oa in ((dest, dest_oa), (src, src_oa)):
                _dump_private(
                    _config.os.path.join(
                        _credentials.AUTH_BACKUPS,
                        "%d-%s.json" % (ts, _config.os.path.basename(p)),
                    ),
                    {
                        "engine": "claude",
                        "blob": _credentials.read_claude_cred_blob(p) or "",
                        "oauth_account": oa,
                        "ts": ts,
                    },
                )
            if not _solo._swap_claude_auth(dest, src):
                _models.err(
                    "neomax: FAILED to swap credentials between %s and %s" % (dest, src)
                )
                _config.sys.exit(1)
            print(
                "rotate-auth: SWAPPED %s ⇄ %s — %s now runs %s, %s now runs %s (no duplicate accounts). Each live session adopts at its next token refresh (no /login)."
                % (
                    _config.os.path.basename(dest),
                    _config.os.path.basename(src),
                    _config.os.path.basename(dest),
                    src_oa.get("emailAddress") or _config.os.path.basename(src),
                    _config.os.path.basename(src),
                    dest_oa.get("emailAddress") or _config.os.path.basename(dest),
                )
            )
        _credentials._log_auth_rotation(
            {
                "ts": ts,
                "engine": engine,
                "dest": _config.os.path.basename(dest),
                "src": _config.os.path.basename(src),
                "from_email": (
                    _credentials._read_oauth_account(src) if engine != "codex" else {}
                ).get("emailAddress"),
                "to_email": (
                    _credentials._read_oauth_account(dest) if engine != "codex" else {}
                ).get("emailAddress"),
                "reason": (reason or "manual") + " (swap)",
            }
        )
        return
    if engine == "codex":
        try:
            with open(_config.os.path.join(src, "auth.json")) as f:
                blob = f.read()
        except OSError:
            _models.err("neomax: source %s has no auth.json (not logged in)" % src)
            _config.sys.exit(1)
        old = None
        try:
            with open(_config.os.path.join(dest, "auth.json")) as f:
                old = f.read()
        except OSError:
            pass
        _dump_private(
            _config.os.path.join(
                _credentials.AUTH_BACKUPS,
                "%d-%s.json" % (ts, _config.os.path.basename(dest)),
            ),
            {"engine": "codex", "blob": old or "", "ts": ts},
        )
        _config.os.makedirs(dest, exist_ok=True)
        with open(_config.os.path.join(dest, "auth.json"), "w") as f:
            f.write(blob)
        from_email = to_email = None
    else:
        blob = _credentials.read_claude_cred_blob(src)
        if not blob:
            _models.err(
                "neomax: source %s has no valid credentials (log it in first)" % src
            )
            _config.sys.exit(1)
        src_oa = _credentials._read_oauth_account(src)
        dest_oa = _credentials._read_oauth_account(dest)
        if src_oa.get("accountUuid") and src_oa.get("accountUuid") == dest_oa.get(
            "accountUuid"
        ):
            _models.err(
                "neomax: dest is ALREADY the same account (%s) — nothing to rotate"
                % (src_oa.get("emailAddress") or src_oa["accountUuid"])
            )
            _config.sys.exit(2)
        old = _credentials.read_claude_cred_blob(dest)
        _dump_private(
            _config.os.path.join(
                _credentials.AUTH_BACKUPS,
                "%d-%s.json" % (ts, _config.os.path.basename(dest)),
            ),
            {"engine": "claude", "blob": old or "", "oauth_account": dest_oa, "ts": ts},
        )
        _config.os.makedirs(dest, exist_ok=True)
        if not _credentials.write_claude_cred_blob(dest, blob):
            _models.err("neomax: FAILED to write credentials into %s" % dest)
            _config.sys.exit(1)
        if src_oa:
            _credentials._write_oauth_account(dest, src_oa)
        from_email = dest_oa.get("emailAddress")
        to_email = src_oa.get("emailAddress")
        for c in ("claude-%s.json", "email-%s.json"):
            try:
                _config.os.remove(
                    _config.os.path.join(
                        _config.USAGE_DIR, c % _config.os.path.basename(dest)
                    )
                )
            except OSError:
                pass
    _credentials._log_auth_rotation(
        {
            "ts": ts,
            "engine": engine,
            "dest": _config.os.path.basename(dest),
            "src": _config.os.path.basename(src),
            "from_email": from_email,
            "to_email": to_email,
            "reason": reason or "manual",
        }
    )
    print(
        "rotate-auth: %s now carries %s's credentials (was %s). Backup saved; restore with: neomax rotate-auth --restore %s%s\nLive session on this profile adopts at its next token refresh (interactive window: /login = instant); new processes use it immediately."
        % (
            dest,
            to_email or _config.os.path.basename(src),
            from_email or "previous",
            dest_sel[0],
            " --engine codex" if engine == "codex" else "",
        )
    )


def claude_account_email(profile):
    """The email of the account this profile's TOKEN actually belongs to — fetched from
    the OAuth profile endpoint with the same token the usage uses. A fallback only;
    display identity comes from _claude_local_identity (instant). Cached EMAIL_FRESH_S."""
    from . import config as _config
    from . import http_client as _http_client

    cache = _config.os.path.join(
        _config.USAGE_DIR, "email-%s.json" % _config.os.path.basename(profile)
    )
    now = _config.time.time()
    try:
        with open(cache) as f:
            d = _config.json.load(f)
        if now - (d.get("observed_at") or 0) < _http_client.EMAIL_FRESH_S:
            return d.get("email")
    except (OSError, ValueError, AttributeError):
        pass
    token = _http_client.claude_oauth_token(profile)
    if not token:
        return None
    try:
        d = _http_client.http_get_json(
            _http_client.CLAUDE_PROFILE_URL,
            {
                "Authorization": "Bearer " + token,
                "anthropic-beta": "oauth-2025-04-20",
                "anthropic-version": "2023-06-01",
            },
            _http_client.USAGE_HTTP_TIMEOUT_S,
        )
    except Exception:
        return None
    email = (d.get("account") or {} if isinstance(d, dict) else {}).get("email") or (
        d.get("email") if isinstance(d, dict) else None
    )
    if email:
        try:
            _config.os.makedirs(_config.USAGE_DIR, exist_ok=True)
            tmp = cache + ".tmp"
            with open(tmp, "w") as f:
                _config.json.dump({"email": email, "observed_at": int(now)}, f)
            _config.os.rename(tmp, cache)
        except OSError:
            pass
    return email
