"""Profile discovery and provider authentication state."""

from . import config as _config


def keychain_service(profile):
    from . import config as _config

    if profile == _config.os.path.join(_config.HOME, ".claude"):
        return "Claude Code-credentials"
    h = _config.hashlib.sha256(profile.encode()).hexdigest()[:8]
    return "Claude Code-credentials-" + h


def opencode_auth_path(profile):
    """OpenCode Go credential file for a Neomax profile.

    Account 1 is represented inside the harness as ~/.opencode while the upstream
    CLI keeps its default data at ~/.local/share/opencode. Extra profiles ARE their
    XDG_DATA_HOME, so their credential is <profile>/opencode/auth.json.
    """
    from . import config as _config
    from . import models as _models

    if _config.os.path.abspath(profile) == _config.os.path.abspath(
        _models.ENGINES["opencode"]["default_dir"]
    ):
        return _config.os.path.join(
            _config.HOME, ".local", "share", "opencode", "auth.json"
        )
    return _config.os.path.join(
        _config.os.path.abspath(profile), "opencode", "auth.json"
    )


def kimi_auth_method(profile):
    from . import config as _config

    try:
        with open(_config.os.path.join(profile, "credentials", "kimi-code.json")) as f:
            item = _config.json.load(f) or {}
        if item.get("access_token") or item.get("refresh_token"):
            return "OAuth"
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(_config.os.path.join(profile, "config.toml"), encoding="utf-8") as f:
            config = f.read()
        if _config.re.search(
            "^\\s*api_key\\s*=\\s*[\"\\'][^\"\\']+[\"\\']\\s*$", config, _config.re.M
        ):
            return "API key"
    except OSError:
        pass
    return None


def logged_in(profile, engine="claude"):
    from . import config as _config

    if engine == "codex":
        auth = _config.os.path.join(profile, "auth.json")
        if not _config.os.path.isfile(auth):
            return False
        try:
            with open(auth) as f:
                d = _config.json.load(f)
        except (OSError, ValueError):
            return False
        return bool(
            d.get("OPENAI_API_KEY")
            or (d.get("tokens") or {}).get("access_token")
            or d.get("tokens")
        )
    if engine == "opencode":
        try:
            with open(opencode_auth_path(profile)) as f:
                store = _config.json.load(f) or {}
            return any(
                (
                    isinstance(item, dict) and bool(item.get("key"))
                    for item in store.values()
                )
            )
        except (OSError, ValueError, AttributeError):
            return False
    if engine == "kimi":
        return kimi_auth_method(profile) == "OAuth"
    if engine == "grok":
        try:
            with open(_config.os.path.join(profile, "auth.json")) as f:
                store = _config.json.load(f) or {}
            return any(
                (
                    isinstance(item, dict) and bool(item.get("key"))
                    for item in store.values()
                )
            )
        except (OSError, ValueError, AttributeError):
            return False
    try:
        r = _config.subprocess.run(
            ["security", "find-generic-password", "-s", keychain_service(profile)],
            capture_output=True,
        )
        if r.returncode == 0:
            return True
    except FileNotFoundError:
        pass
    return _config.os.path.isfile(_config.os.path.join(profile, ".credentials.json"))


OPENCODE_RATE_RE = _config.re.compile(
    "rate.?limit|too many requests|\\b429\\b|tokens per min|\\btpm\\b", _config.re.I
)
