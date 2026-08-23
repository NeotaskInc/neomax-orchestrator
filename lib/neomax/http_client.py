"""TLS, HTTP, and Claude OAuth access."""

from . import config as _config

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
EMAIL_FRESH_S = 3600
USAGE_FRESH_S = 60
USAGE_STALE_LABEL_S = 300
USAGE_RL_BACKOFF_S = 120
USAGE_STALE_OK_S = 24 * 3600
USAGE_HTTP_TIMEOUT_S = 8
_SSL_CTX = None


def ssl_context():
    """A TLS context that can actually verify certs. The python.org macOS framework
    build ships with an EMPTY default CA path (no etc/openssl/cert.pem unless the
    'Install Certificates.command' was ever run) → ssl.create_default_context() then
    trusts nothing and every https fetch dies with CERTIFICATE_VERIFY_FAILED, which
    silently flips the whole usage board to 'unknown'. So: take the default context,
    and if it loaded no CA certs, fall back to certifi's bundle (always shipped with
    pip). Cached at module level; never raises — worst case returns the bare default."""
    from . import config as _config

    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    ctx = _config.ssl.create_default_context()
    try:
        has_ca = bool(ctx.cert_store_stats().get("x509_ca"))
    except Exception:
        has_ca = True
    if not has_ca:
        for loc in (_config.os.environ.get("SSL_CERT_FILE"), _certifi_where()):
            if not loc:
                continue
            try:
                ctx.load_verify_locations(loc)
                break
            except Exception:
                continue
    _SSL_CTX = ctx
    return ctx


def _certifi_where():
    """certifi's CA bundle path, or None if certifi isn't importable."""
    try:
        import certifi

        return certifi.where()
    except Exception:
        return None


def http_get_json(url, headers, timeout):
    """GET → parsed JSON. Module-level seam so tests can stub the network."""
    from . import config as _config

    req = _config.urllib.request.Request(url, headers=headers)
    with _config.urllib.request.urlopen(
        req, timeout=timeout, context=ssl_context()
    ) as resp:
        return _config.json.loads(resp.read().decode("utf-8", "replace"))


def http_post_json(url, payload, headers, timeout):
    """POST a JSON body → parsed JSON. Module-level seam so tests can stub the network."""
    from . import config as _config

    data = _config.json.dumps(payload).encode("utf-8")
    hdrs = dict(headers or {})
    hdrs.setdefault("Content-Type", "application/json")
    req = _config.urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with _config.urllib.request.urlopen(
        req, timeout=timeout, context=ssl_context()
    ) as resp:
        return _config.json.loads(resp.read().decode("utf-8", "replace"))


CLAUDE_OAUTH_TOKEN_URL = _config.os.environ.get(
    "NEOMAX_OAUTH_TOKEN_URL", "https://platform.claude.com/v1/oauth/token"
)
CLAUDE_OAUTH_CLIENT_ID = _config.os.environ.get(
    "NEOMAX_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
)
CLAUDE_OAUTH_USER_AGENT = _config.os.environ.get(
    "NEOMAX_OAUTH_UA", "claude-cli/2.1.56 (external, cli)"
)
REFRESH_ATTEMPT_COOLDOWN_S = 120


def iso_to_epoch(value):
    """Epoch seconds from an ISO-8601 string (the usage API's resets_at) or a
    numeric epoch (any unit); None if unparseable."""
    from . import account_state as _account_state
    from . import config as _config

    e = _account_state.to_epoch_seconds(value)
    if e is not None:
        return e
    try:
        s = str(value).strip().replace("Z", "+00:00")
        return _config.datetime.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def claude_oauth_token(profile):
    """This profile's OWN OAuth access token. The Keychain entry is namespaced per
    config dir (default → 'Claude Code-credentials', others get a path-hash suffix);
    the live CLI's item uses the OS username as the item account — query that first
    (a stale 'Claude Code'-account duplicate from older CLI versions can shadow it),
    then any-account, then the profile's .credentials.json. Expired tokens are
    skipped (the API would 401). None when no valid token. Never raises."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config

    svc = _account_auth.keychain_service(profile)
    blobs = []
    user = _config.os.environ.get("USER")
    for extra in ([["-a", user]] if user else []) + [[]]:
        try:
            r = _config.subprocess.run(
                ["security", "find-generic-password", "-s", svc] + extra + ["-w"],
                capture_output=True,
                text=True,
                timeout=USAGE_HTTP_TIMEOUT_S,
            )
            if r.returncode == 0 and r.stdout.strip():
                blobs.append(r.stdout.strip())
        except (FileNotFoundError, _config.subprocess.TimeoutExpired, OSError):
            pass
    try:
        with open(_config.os.path.join(profile, ".credentials.json")) as f:
            blobs.append(f.read())
    except OSError:
        pass
    (best, best_exp) = (None, 0)
    for blob in blobs:
        try:
            oa = (_config.json.loads(blob) or {}).get("claudeAiOauth") or {}
        except (ValueError, AttributeError):
            continue
        tok = oa.get("accessToken")
        exp = _account_state.to_epoch_seconds(oa.get("expiresAt"))
        if tok and exp and (exp > _config.time.time()) and (exp > best_exp):
            (best, best_exp) = (tok, exp)
    return best


def _stored_oauth_blob(profile):
    """The profile's stored claudeAiOauth dict (keychain first, else .credentials.json) EVEN IF
    the access token is expired — used by the refresher, which needs the refresh token. None if
    no parseable blob. Returns (oauth_dict, full_blob_dict)."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config

    blobs = []
    user = _config.os.environ.get("USER")
    svc = _account_auth.keychain_service(profile)
    for extra in ([["-a", user]] if user else []) + [[]]:
        try:
            r = _config.subprocess.run(
                ["security", "find-generic-password", "-s", svc] + extra + ["-w"],
                capture_output=True,
                text=True,
                timeout=USAGE_HTTP_TIMEOUT_S,
            )
            if r.returncode == 0 and r.stdout.strip():
                blobs.append(r.stdout.strip())
        except (FileNotFoundError, _config.subprocess.TimeoutExpired, OSError):
            pass
    try:
        with open(_config.os.path.join(profile, ".credentials.json")) as f:
            blobs.append(f.read())
    except OSError:
        pass
    best = None
    for b in blobs:
        try:
            full = _config.json.loads(b) or {}
            oa = full.get("claudeAiOauth") or {}
        except (ValueError, AttributeError):
            continue
        if oa.get("refreshToken"):
            if best is None or (
                _account_state.to_epoch_seconds(oa.get("expiresAt")) or 0
            ) > (_account_state.to_epoch_seconds(best[0].get("expiresAt")) or 0):
                best = (oa, full)
    return best if best else (None, None)


def claude_refresh_token(profile, force=False):
    """Refresh this account's OAuth access token IN PLACE using its refresh token — the SAME
    exchange the interactive CLI does, so an IDLE account (no live session to refresh it) stays
    maintained instead of showing 'token expired'. Writes the new access+refresh token back to
    BOTH stores (keychain + .credentials.json), preserving the rest of the blob. Returns the new
    access token, or None on any failure (old creds untouched). Debounced per account; skips an
    account with a live session (that session owns its own refresh — don't race its rotation)."""
    from . import config as _config
    from . import credentials as _credentials
    from . import rotation_commands as _rotation_commands

    (oa, full) = _stored_oauth_blob(profile)
    if not oa or not oa.get("refreshToken"):
        return None
    if not force and _rotation_commands._profile_recently_active(profile, within_s=180):
        return None
    att = _config.os.path.join(
        _config.USAGE_DIR, "refresh-%s.json" % _config.os.path.basename(profile)
    )
    now = _config.time.time()
    if not force:
        try:
            with open(att) as f:
                if (
                    now - (_config.json.load(f).get("ts") or 0)
                    < REFRESH_ATTEMPT_COOLDOWN_S
                ):
                    return None
        except (OSError, ValueError, AttributeError):
            pass
    try:
        _config.os.makedirs(_config.USAGE_DIR, exist_ok=True)
        with open(att, "w") as f:
            _config.json.dump({"ts": int(now)}, f)
    except OSError:
        pass
    try:
        resp = http_post_json(
            CLAUDE_OAUTH_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": oa["refreshToken"],
                "client_id": CLAUDE_OAUTH_CLIENT_ID,
            },
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": CLAUDE_OAUTH_USER_AGENT,
            },
            USAGE_HTTP_TIMEOUT_S,
        )
    except Exception:
        return None
    new_access = (resp or {}).get("access_token") if isinstance(resp, dict) else None
    if not new_access:
        return None
    new_oa = dict(oa)
    new_oa["accessToken"] = new_access
    if resp.get("refresh_token"):
        new_oa["refreshToken"] = resp["refresh_token"]
    expires_in = resp.get("expires_in")
    if expires_in:
        new_oa["expiresAt"] = int((now + float(expires_in)) * 1000)
    new_full = dict(full or {})
    new_full["claudeAiOauth"] = new_oa
    if not _credentials.write_claude_cred_blob(profile, _config.json.dumps(new_full)):
        return None
    try:
        _config.os.remove(
            _config.os.path.join(
                _config.USAGE_DIR, "claude-%s.json" % _config.os.path.basename(profile)
            )
        )
    except OSError:
        pass
    return new_access


def _claude_local_identity(profile):
    """The account a Claude profile is signed into, read from its .claude.json
    oauthAccount — LOCAL + INSTANT (Claude Code rewrites it on every /login, so it always
    reflects the current account). Returns {email, name, uuid} ('' when absent). This is
    the authoritative display identity — no network, no cache lag after a re-login."""
    from . import config as _config

    cfg = (
        _config.os.path.join(_config.HOME, ".claude.json")
        if profile == _config.os.path.join(_config.HOME, ".claude")
        else _config.os.path.join(profile, ".claude.json")
    )
    try:
        with open(cfg) as f:
            oa = (_config.json.load(f) or {}).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError):
        return {"email": "", "name": "", "uuid": ""}
    return {
        "email": oa.get("emailAddress") or "",
        "name": oa.get("displayName") or "",
        "uuid": oa.get("accountUuid") or "",
    }
