"""Provider model resolution, profiles, and OpenCode policy."""

from . import config as _config


def _explicit_model(name, flag):
    n = (name or "").strip()
    if not n or any((ch.isspace() or ord(ch) < 32 for ch in n)):
        err("neomax: %s requires a non-empty model ID without whitespace" % flag)
        raise SystemExit(2)
    return n


def resolve_claude_model(name=None):
    from . import config as _config

    return (
        _explicit_model(name, "--claude-model")
        if name
        else _config.os.environ.get("NEOMAX_CLAUDE_MODEL")
        or _config.CLAUDE_DEFAULT_MODEL_1M
    )


def resolve_codex_model(name=None):
    """Keep the convenient 5.6 aliases; pass any other explicit model to Codex."""
    from . import config as _config

    if not name:
        return _config.os.environ.get("NEOMAX_CODEX_MODEL") or _config.CODEX_MODEL
    raw = _explicit_model(name, "--codex-model")
    n = raw.lower()
    if not n:
        return _config.CODEX_MODEL
    if n in _config.CODEX_MODELS:
        return _config.CODEX_MODELS[n]
    if n in ("gpt-5.6", "gpt5.6"):
        return _config.CODEX_MODELS[_config.CODEX_MODEL_DEFAULT_TIER]
    return raw


def codex_model_tier(model):
    """A gpt-5.6 slug → its short tier nickname ('sol'/'terra'/'luna'), else None. Used for
    compact labels/telemetry so the dashboard doesn't have to re-parse slugs."""
    from . import config as _config

    for tier, slug in _config.CODEX_MODELS.items():
        if slug == model:
            return tier
    return None


CODEX_SERVICE_TIER = "fast"
CODEX_EFFORTS = ("low", "medium", "high", "xhigh")
OPENCODE_MODEL = "opencode-go/ox-alpha-free"
OPENCODE_POLICY_FILE = _config.os.path.join(
    _config.NEOMAX_REPO_DIR, "opencode", "model-policy.json"
)
KIMI_MODELS = {"k3": "kimi-code/k3", "k2.7": "kimi-code/kimi-for-coding"}
KIMI_MODEL = KIMI_MODELS["k3"]
GROK_MODEL = "grok-4.6"


def resolve_opencode_model(name=None):
    from . import config as _config

    raw = (
        _explicit_model(name, "--opencode-model")
        if name
        else _config.os.environ.get("NEOMAX_OPENCODE_MODEL") or OPENCODE_MODEL
    )
    if "/" not in raw or not all(raw.split("/", 1)):
        err(
            "neomax: OpenCode models must use the local registry's provider/model form, got %r"
            % raw
        )
        raise SystemExit(2)
    return raw


def resolve_kimi_model(name=None):
    from . import config as _config

    if not name:
        return _config.os.environ.get("NEOMAX_KIMI_MODEL") or KIMI_MODEL
    raw = _explicit_model(name, "--kimi-model")
    n = raw.lower()
    aliases = {"k27": "k2.7", "2.7": "k2.7", "kimi-k3": "k3"}
    n = aliases.get(n, n)
    if n in KIMI_MODELS:
        return KIMI_MODELS[n]
    return raw


def resolve_grok_model(name=None):
    from . import config as _config

    return (
        _explicit_model(name, "--grok-model")
        if name
        else _config.os.environ.get("NEOMAX_GROK_MODEL") or GROK_MODEL
    )


def default_model(engine):
    return {
        "claude": resolve_claude_model,
        "codex": resolve_codex_model,
        "opencode": resolve_opencode_model,
        "kimi": resolve_kimi_model,
        "grok": resolve_grok_model,
    }[engine]()


def opencode_policy_content(model=None):
    """Validate the safety template, then pin every OpenCode agent to the selected model."""
    from . import config as _config

    try:
        with open(OPENCODE_POLICY_FILE) as f:
            data = _config.json.load(f)
        if (
            data.get("model") != OPENCODE_MODEL
            or data.get("small_model") != OPENCODE_MODEL
        ):
            raise ValueError("model pins do not match")
        if data.get("enabled_providers") != ["opencode-go"]:
            raise ValueError("provider allowlist is not OpenCode Go only")
        agents = data.get("agent") or {}
        required_agents = {
            "build",
            "plan",
            "general",
            "explore",
            "scout",
            "compaction",
            "title",
            "summary",
        }
        if set(agents) != required_agents or any(
            ((cfg or {}).get("model") != OPENCODE_MODEL for cfg in agents.values())
        ):
            raise ValueError("every built-in agent must be pinned to OX Alpha")
        if data.get("share") != "disabled":
            raise ValueError("session sharing must be disabled")
        selected = resolve_opencode_model(model)
        provider = selected.split("/", 1)[0]
        data["model"] = data["small_model"] = selected
        data["enabled_providers"] = [provider]
        for cfg in agents.values():
            cfg["model"] = selected
        return _config.json.dumps(data, separators=(",", ":"))
    except (OSError, ValueError, TypeError) as exc:
        err(
            "neomax: refusing OpenCode launch — invalid policy template %s: %s"
            % (OPENCODE_POLICY_FILE, exc)
        )
        raise SystemExit(2)


ENGINES = {
    "claude": {
        "bin": _config.CLAUDE_BIN,
        "config_env": "CLAUDE_CONFIG_DIR",
        "default_dir": _config.os.path.join(_config.HOME, ".claude"),
        "profile_glob": ".claude-acct*",
        "procname": "claude",
        "pgrep": ("-x", "claude"),
        "default_unset_env": True,
        "scrub": (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
        ),
        "test_env": "NEOMAX_PROFILES",
        "orch_dir": _config.os.path.join(_config.HOME, ".claude-orch"),
        "orch_test_env": "NEOMAX_CLAUDE_ORCH",
    },
    "codex": {
        "bin": _config.CODEX_BIN,
        "config_env": "CODEX_HOME",
        "default_dir": _config.os.path.join(_config.HOME, ".codex"),
        "profile_glob": ".codex-acct*",
        "procname": "codex",
        "pgrep": ("-f", "codex exec"),
        "default_unset_env": False,
        "scrub": ("OPENAI_API_KEY", "CODEX_API_KEY"),
        "test_env": "NEOMAX_CODEX_PROFILES",
        "orch_dir": _config.os.path.join(_config.HOME, ".codex-orch"),
        "orch_test_env": "NEOMAX_CODEX_ORCH",
    },
    "opencode": {
        "bin": _config.OPENCODE_BIN,
        "config_env": "XDG_DATA_HOME",
        "default_dir": _config.os.path.join(_config.HOME, ".opencode"),
        "profile_glob": ".opencode-acct*",
        "procname": "opencode",
        "pgrep": ("-f", "opencode run"),
        "default_unset_env": True,
        "scrub": ("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY", "OPENAI_API_KEY"),
        "test_env": "NEOMAX_OPENCODE_PROFILES",
        "orch_dir": _config.os.path.join(_config.HOME, ".opencode-orch"),
        "orch_test_env": "NEOMAX_OPENCODE_ORCH",
    },
    "kimi": {
        "bin": _config.KIMI_BIN,
        "config_env": "KIMI_CODE_HOME",
        "default_dir": _config.os.path.join(_config.HOME, ".kimi-code"),
        "profile_glob": ".kimi-code-acct*",
        "procname": "kimi",
        "pgrep": ("-f", "kimi.*(--prompt|-p)"),
        "default_unset_env": True,
        "scrub": (
            "KIMI_API_KEY",
            "KIMI_MODEL_API_KEY",
            "KIMI_MODEL_BASE_URL",
            "KIMI_MODEL_NAME",
            "KIMI_CODE_BASE_URL",
            "KIMI_BASE_URL",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        "test_env": "NEOMAX_KIMI_PROFILES",
        "orch_dir": _config.os.path.join(_config.HOME, ".kimi-code-orch"),
        "orch_test_env": "NEOMAX_KIMI_ORCH",
    },
    "grok": {
        "bin": _config.GROK_BIN,
        "config_env": "GROK_HOME",
        "default_dir": _config.os.path.join(_config.HOME, ".grok"),
        "profile_glob": ".grok-acct*",
        "procname": "grok",
        "pgrep": ("-f", "grok.*(--single|-p)"),
        "default_unset_env": True,
        "scrub": (
            "XAI_API_KEY",
            "GROK_API_KEY",
            "GROK_DEPLOYMENT_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        "test_env": "NEOMAX_GROK_PROFILES",
        "orch_dir": _config.os.path.join(_config.HOME, ".grok-orch"),
        "orch_test_env": "NEOMAX_GROK_ORCH",
    },
}


def orch_profile(engine):
    """The DEDICATED-orchestrator profile dir for any supported engine — a separate account
    RESERVED for orchestration so coordinating the
    fleet always has quota. None when not set up (dir absent) → everything falls back
    to the normal behavior. Authenticate it with that engine's account helper."""
    from . import config as _config

    cfg = ENGINES[engine]
    env = _config.os.environ.get(cfg.get("orch_test_env") or "")
    if env:
        return _config.os.path.abspath(env)
    d = cfg.get("orch_dir")
    return d if d and _config.os.path.isdir(d) else None


def orch_reserved():
    """True when this session was launched with --orchestrator (provider launchers set
    NEOMAX_ORCH_RESERVED=1): the dedicated orchestrator account is RESERVED — excluded
    from the worker pool, used only to orchestrate. Without the flag the orch account
    (if it exists) simply joins the pool as an extra worker account."""
    from . import config as _config

    return _config.os.environ.get("NEOMAX_ORCH_RESERVED") == "1"


def engine_profiles(engine):
    """Profile dirs for an engine, account 1 first. Dynamic: discovers any number
    of acctN dirs by globbing (no hardcoded count). Sorted NUMERICALLY so acct10
    follows acct9 (not lexicographically, where acct10 would sort before acct2).
    The dedicated orchestrator profile (if set up) is appended LAST — present for
    status/keepalive/usage tracking; worker SELECTION filters it when reserved."""
    from . import config as _config

    cfg = ENGINES[engine]
    env = _config.os.environ.get(cfg["test_env"])
    if env:
        profs = [_config.os.path.abspath(p) for p in env.split(":") if p]
    else:

        def acct_num(path):
            m = _config.re.search("acct(\\d+)$", path)
            return int(m.group(1)) if m else 0

        extras = sorted(
            _config.globmod.glob(
                _config.os.path.join(_config.HOME, cfg["profile_glob"])
            ),
            key=acct_num,
        )
        profs = [cfg["default_dir"]] + extras
    op = orch_profile(engine)
    if op and op not in profs:
        profs.append(op)
    return profs


def worker_profiles(engine):
    """The profiles workers may run on: all accounts, MINUS the dedicated orchestrator
    account when this session reserved it (--orchestrator). Unreserved → the orch
    account is just another pool member (7 accounts, one of them orchestrating)."""
    profs = engine_profiles(engine)
    if orch_reserved():
        op = orch_profile(engine)
        profs = [p for p in profs if p != op]
    return profs


WORKER_DIRECTIVE = "You are a headless delegated worker with full autonomy: never ask the user questions — decide and proceed. End with a concise factual report of what was done, what was verified, and any failures."


def codex_goal_block(goal, max_turns=None):
    cap = ""
    if max_turns:
        cap = (
            " Make at most %s rounds of self-correction; if the objective still is not met, stop and report exactly what remains."
            % max_turns
        )
    return (
        "OBJECTIVE — do not finish until this condition holds:\n%s\nWork autonomously toward it, then VERIFY the condition is actually met (run the relevant checks/tests/builds) before you end. If verification fails, keep working until it passes.%s"
        % (goal, cap)
    )


CONTINUATION_NOTE = "\n\nNOTE: a previous attempt at this task may have left partial work in the current directory. Run `git status` (if a repo) and inspect existing files first, then continue the task to completion rather than starting over."
LIMIT_RE = _config.re.compile(
    "usage limit|out of extra usage|rate.?limit|5-hour limit|weekly limit|credit balance is too low",
    _config.re.I,
)
INTERRUPT_RE = _config.re.compile(
    "request was aborted|interrupted by user|all fibers interrupted", _config.re.I
)


def profiles(engine="claude"):
    return engine_profiles(engine)


def err(msg):
    from . import config as _config

    print(msg, file=_config.sys.stderr, flush=True)
