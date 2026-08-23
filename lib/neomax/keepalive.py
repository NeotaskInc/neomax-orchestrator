"""Provider credential keepalive policy."""

from . import config as _config


def _claude_token_expiry(profile):
    """Epoch expiry of this Claude profile's stored OAuth access token (newest valid
    blob), or None if unreadable. Same sources as claude_oauth_token. Tolerant of the
    intermittent keychain read — returns None (skip) rather than failing."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import http_client as _http_client

    svc = _account_auth.keychain_service(profile)
    blobs = []
    user = _config.os.environ.get("USER")
    for extra in ([["-a", user]] if user else []) + [[]]:
        try:
            r = _config.subprocess.run(
                ["security", "find-generic-password", "-s", svc] + extra + ["-w"],
                capture_output=True,
                text=True,
                timeout=_http_client.USAGE_HTTP_TIMEOUT_S,
            )
            if r.returncode == 0 and r.stdout.strip():
                blobs.append(r.stdout.strip())
        except (FileNotFoundError, _config.subprocess.TimeoutExpired, OSError):
            pass
    try:
        blobs.append(open(_config.os.path.join(profile, ".credentials.json")).read())
    except OSError:
        pass
    best = None
    for blob in blobs:
        try:
            oa = (_config.json.loads(blob) or {}).get("claudeAiOauth") or {}
        except (ValueError, AttributeError):
            continue
        exp = _account_state.to_epoch_seconds(oa.get("expiresAt"))
        if oa.get("refreshToken") and exp and (best is None or exp > best):
            best = exp
    return best


def _codex_token_expiry(profile):
    """Epoch expiry of this Codex profile's ACCESS token (the JWT that actually authorizes
    API calls — long-lived, days; NOT the id_token, which is just an identity claim and
    expires separately), or None. Codex also refreshes file-based on use, so it rarely
    needs keep-alive."""
    from . import account_state as _account_state
    from . import config as _config

    try:
        d = _config.json.load(open(_config.os.path.join(profile, "auth.json")))
        seg = ((d.get("tokens") or {}).get("access_token") or "").split(".")
        if len(seg) >= 2:
            pl = _config.json.loads(
                _config.base64.urlsafe_b64decode(seg[1] + "=" * (-len(seg[1]) % 4))
            )
            return _account_state.to_epoch_seconds(pl.get("exp"))
    except Exception:
        pass
    return None


def keepalive_account(profile, engine):
    """Run ONE minimal native CLI turn on this account so the CLI refreshes + PERSISTS its
    token exactly like a normal session does (reliable + safe — the CLI owns keychain
    access and writes back atomically; far safer than hand-rolling OAuth + a keychain
    write that could half-complete and brick the account). Detached; bounded; cheap."""
    from . import config as _config
    from . import models as _models

    env = dict(_config.os.environ)
    for k in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    ):
        env.pop(k, None)
    try:
        log = open(_config.os.path.join(_config.STATE_DIR, "keepalive.log"), "ab")
    except OSError:
        log = _config.subprocess.DEVNULL
    if engine == "claude":
        if profile == _models.ENGINES["claude"]["default_dir"]:
            env.pop("CLAUDE_CONFIG_DIR", None)
        else:
            env["CLAUDE_CONFIG_DIR"] = profile
        args = [
            _config.CLAUDE_BIN,
            "-p",
            "--model",
            _config.CLAUDE_DEFAULT_MODEL_1M,
            "--dangerously-skip-permissions",
            "--max-turns",
            "1",
            "Reply with exactly: OK",
        ]
    else:
        env["CODEX_HOME"] = _config.os.path.abspath(profile)
        args = [
            _config.CODEX_BIN,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-C",
            _config.tempfile.gettempdir(),
            "-m",
            _config.CODEX_MODEL,
            "-c",
            "model_reasoning_effort=low",
            "-c",
            "service_tier=%s" % _models.CODEX_SERVICE_TIER,
            "-s",
            "read-only",
            "Reply with exactly: OK",
        ]
    try:
        _config.subprocess.Popen(
            args,
            env=env,
            stdout=log,
            stderr=log,
            stdin=_config.subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except (FileNotFoundError, OSError):
        return False
    finally:
        if log is not _config.subprocess.DEVNULL:
            try:
                log.close()
            except OSError:
                pass


def keepalive_pass(state):
    """Refresh idle accounts whose token is near/\ufeffpast expiry. Skips accounts with a live
    session (the CLI keeps those fresh) and rate-limits per account. Returns # refreshed.
    State key 'ka' maps profile → last-attempt epoch."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import models as _models
    from . import usage_watch as _usage_watch

    now = _config.time.time()
    ka = state.setdefault("ka", {})
    done = 0
    for engine in ("claude", "codex"):
        live = _account_state.live_counts(engine)
        for p in _models.engine_profiles(engine):
            if not _account_auth.logged_in(p, engine):
                continue
            if now - ka.get(p, 0) < _usage_watch.KEEPALIVE_GAP_S:
                continue
            exp = (_claude_token_expiry if engine == "claude" else _codex_token_expiry)(
                p
            )
            if exp is None or exp > now + _usage_watch.KEEPALIVE_MARGIN_S:
                continue
            if keepalive_account(p, engine):
                ka[p] = now
                done += 1
                _models.err(
                    "neomax keepalive: refreshing %s %s (token near expiry%s)"
                    % (
                        engine,
                        _config.os.path.basename(p),
                        ", live session" if live.get(p, 0) else "",
                    )
                )
    return done


ROTATE_TICK_EVERY_S = float(_config.os.environ.get("NEOMAX_ROTATE_TICK", "30"))
ROTATE_TICK_ACTIVE_GRACE_S = float(_config.os.environ.get("NEOMAX_ROTATE_GRACE", "45"))
