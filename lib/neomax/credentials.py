"""Credential storage, identity metadata, and rotation records."""

from . import config as _config

AUTH_BACKUPS = _config.os.path.join(_config.STATE_DIR, "auth-backups")
AUTH_ROTATIONS = _config.os.path.join(_config.STATE_DIR, "auth-rotations.jsonl")


def read_claude_cred_blob(profile):
    """This profile's FULL credential blob (the JSON with claudeAiOauth access+refresh
    tokens): newest valid from keychain (user-account item first), else .credentials.json.
    Returns the raw JSON string or None. Read-only; never raises."""
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
        with open(_config.os.path.join(profile, ".credentials.json")) as f:
            blobs.append(f.read().strip())
    except OSError:
        pass
    (best, best_exp) = (None, -1)
    for b in blobs:
        try:
            oa = (_config.json.loads(b) or {}).get("claudeAiOauth") or {}
        except (ValueError, AttributeError):
            continue
        if not oa.get("accessToken"):
            continue
        exp = _account_state.to_epoch_seconds(oa.get("expiresAt")) or 0
        if exp > best_exp:
            (best, best_exp) = (b, exp)
    return best


def write_claude_cred_blob(profile, blob):
    """Write a credential blob into a profile's BOTH stores (keychain item updated in
    place with -U, and .credentials.json 0600) so any reader finds it. True on success
    of at least the file write (the keychain write can be intermittently denied)."""
    from . import account_auth as _account_auth
    from . import config as _config

    ok = False
    svc = _account_auth.keychain_service(profile)
    user = _config.os.environ.get("USER") or ""
    try:
        r = _config.subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                user,
                "-s",
                svc,
                "-w",
                blob,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ok = r.returncode == 0
    except (FileNotFoundError, _config.subprocess.TimeoutExpired, OSError):
        pass
    try:
        path = _config.os.path.join(profile, ".credentials.json")
        tmp = path + ".tmp"
        fd = _config.os.open(
            tmp, _config.os.O_WRONLY | _config.os.O_CREAT | _config.os.O_TRUNC, 384
        )
        with _config.os.fdopen(fd, "w") as f:
            f.write(blob)
        _config.os.replace(tmp, path)
        ok = True
    except OSError:
        pass
    return ok


def _claude_json_path(profile):
    from . import config as _config

    return (
        _config.os.path.join(_config.HOME, ".claude.json")
        if profile == _config.os.path.join(_config.HOME, ".claude")
        else _config.os.path.join(profile, ".claude.json")
    )


def _read_oauth_account(profile):
    from . import config as _config

    try:
        with open(_claude_json_path(profile)) as f:
            return (_config.json.load(f) or {}).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _write_oauth_account(profile, oa):
    """Update ONLY the oauthAccount key of the profile's .claude.json (merge, never
    clobber the rest — it holds the user's settings/history)."""
    from . import config as _config

    path = _claude_json_path(profile)
    try:
        try:
            with open(path) as f:
                d = _config.json.load(f) or {}
        except (OSError, ValueError):
            d = {}
        d["oauthAccount"] = oa
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            _config.json.dump(d, f, indent=2)
        _config.os.replace(tmp, path)
        return True
    except OSError:
        return False


def _log_auth_rotation(rec):
    from . import config as _config

    try:
        _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
        with open(AUTH_ROTATIONS, "a") as f:
            f.write(_config.json.dumps(rec) + "\n")
    except OSError:
        pass


def recent_auth_rotations(within_s=24 * 3600, limit=10):
    from . import config as _config

    out = []
    try:
        with open(AUTH_ROTATIONS) as f:
            for line in f:
                try:
                    r = _config.json.loads(line)
                except ValueError:
                    continue
                if _config.time.time() - r.get("ts", 0) <= within_s:
                    out.append(r)
    except OSError:
        pass
    return out[-limit:]


def _resolve_profile(sel, engine):
    """Account selector → profile path: N, 'orch', or a literal path."""
    from . import config as _config
    from . import models as _models

    if isinstance(sel, str) and (sel.startswith("/") or sel.startswith("~")):
        return _config.os.path.abspath(_config.os.path.expanduser(sel))
    if str(sel).lower() == "orch":
        return _models.orch_profile(engine)
    try:
        n = int(sel)
    except (TypeError, ValueError):
        return None
    plist = [
        q for q in _models.engine_profiles(engine) if q != _models.orch_profile(engine)
    ]
    return plist[n - 1] if 1 <= n <= len(plist) else None
