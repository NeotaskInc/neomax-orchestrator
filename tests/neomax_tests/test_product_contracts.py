"""Public packaging, provider parity, launch selection, goals, and delegation safety."""

from .support import *


def test_agent_command_surface():
    print("test_agent_command_surface")
    import ast

    cli_path = os.path.join(ROOT, "lib", "neomax", "cli.py")
    tree = ast.parse(open(cli_path).read())
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    module_aliases = {}
    for node in ast.walk(main):
        if isinstance(node, ast.ImportFrom) and node.module is None:
            for alias in node.names:
                module_aliases[alias.asname or alias.name] = alias.name
    targets = set()
    for node in ast.walk(main):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            continue
        alias = node.func.value.id
        if alias in module_aliases:
            targets.add((module_aliases[alias], node.func.attr))
    check(
        targets
        and all(
            callable(getattr(importlib.import_module("neomax." + module), attr, None))
            for module, attr in targets
        ),
        "every Python CLI dispatch target resolves to a callable registered module owner",
    )

    def string_values(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, (ast.Tuple, ast.List)):
            return set().union(*(string_values(item) for item in node.elts))
        return set()

    cli_routes = set()
    for node in ast.walk(main):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        if isinstance(test.left, ast.Name) and test.left.id == "cmd":
            cli_routes.update(string_values(test.comparators[0]))
    shell = open(os.path.join(BIN, "neomax")).read()
    case_match = re.search(r'case "\$\{1:-\}" in\n(.*?)\nesac', shell, re.S)
    shell_routes = set()
    for line in case_match.group(1).splitlines():
        match = re.match(r"\s*([^#][^)]*)\)", line)
        if match:
            shell_routes.update(part.strip() for part in match.group(1).split("|"))
    check(
        shell_routes == ((cli_routes - {"__supervise"}) | {"auto"}),
        "the public shell wrapper reaches every user-facing Python command alias",
    )
    check(
        '${1:-}" == "commands"' in shell,
        "the public wrapper preserves the Python command-registry help alias",
    )


def test_public_product_contract():
    print("test_public_product_contract")
    manifest = json.load(open(os.path.join(ROOT, "package.json")))
    expected = {
        "neomax",
        "cmax",
        "cdx",
        "cdxmax",
        "ocx",
        "ocmax",
        "kmx",
        "kmax",
        "gmx",
        "gmax",
        "neomax-portal",
        "neomax-usage-agent",
        "neomax-worktrees",
    }
    check(
        set(manifest.get("bin", {})) == expected,
        "package exports only supported public commands",
    )
    retired_probe = "ox-" + "smo" + "ke"
    check(
        retired_probe not in manifest.get("bin", {})
        and (not os.path.exists(os.path.join(BIN, retired_probe))),
        "authenticated probe tooling is not published",
    )
    check(
        manifest["bin"].get("neomax") == "bin/neomax"
        and (not any(("lib/" in path for path in manifest["bin"].values()))),
        "public commands use executable wrappers; library modules are not exposed as bins",
    )
    from neomax.module_registry import MODULE_REGISTRY

    registry_names = [spec.name for spec in MODULE_REGISTRY]
    module_dir = os.path.join(ROOT, "lib", "neomax")
    domain_files = sorted(
        (
            os.path.splitext(name)[0]
            for name in os.listdir(module_dir)
            if name.endswith(".py")
            and name not in ("__init__.py", "module_registry.py")
        )
    )
    registered_exports = [symbol for spec in MODULE_REGISTRY for symbol in spec.exports]
    check(
        len(MODULE_REGISTRY) >= 24 and len(registry_names) == len(set(registry_names)),
        "the authoritative registry contains distinct, responsibility-sized domains",
    )
    check(
        sorted(registry_names) == domain_files,
        "every domain module is registered exactly once and no implementation module is orphaned",
    )
    check(
        all((spec.responsibility.strip() and spec.exports for spec in MODULE_REGISTRY)),
        "every registered module declares its responsibility and owned exports",
    )
    check(
        len(registered_exports) == len(set(registered_exports))
        and set(registered_exports) == set(m.__all__),
        "the registry is the unique source of public-symbol ownership",
    )
    check(
        all((importlib.import_module("neomax." + name) for name in registry_names)),
        "every registered module imports through the normal package loader",
    )
    wrapper = open(NEOMAX).read()
    check(
        "from neomax import *" in wrapper and (not re.search("^def ", wrapper, re.M)),
        "neomax_core.py remains a compatibility entrypoint, not a second implementation",
    )
    from neomax_tests.suite_registry import SUITE_REGISTRY, TEST_ORDER

    suite_names = [spec.name for spec in SUITE_REGISTRY]
    suite_dir = os.path.join(ROOT, "tests", "neomax_tests")
    suite_files = sorted(
        (
            os.path.splitext(name)[0]
            for name in os.listdir(suite_dir)
            if name.startswith("test_") and name.endswith(".py")
        )
    )
    registered_tests = [name for spec in SUITE_REGISTRY for name in spec.tests]
    check(
        len(suite_names) == len(set(suite_names))
        and sorted(suite_names) == suite_files,
        "every responsibility-based test module is registered exactly once",
    )
    check(
        all((spec.responsibility.strip() and spec.tests for spec in SUITE_REGISTRY)),
        "every registered test module declares its responsibility and tests",
    )
    check(
        len(registered_tests) == len(set(registered_tests))
        and set(registered_tests) == set(TEST_ORDER),
        "the test registry uniquely owns every test while preserving explicit execution order",
    )
    runner_source = open(os.path.join(ROOT, "tests", "test_neomax.py")).read()
    check(
        "neomax_tests.runner" in runner_source
        and (not re.search("^def test_", runner_source, re.M)),
        "tests/test_neomax.py remains a thin compatibility runner",
    )
    community_files = (
        "AGENTS.md",
        "ARCHITECTURE.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/workflows/test.yml",
    )
    check(
        all((os.path.isfile(os.path.join(ROOT, path)) for path in community_files)),
        "public development, contribution, security, issue, and PR guidance is shipped",
    )
    check(
        "provider-neutral" in open(os.path.join(ROOT, "AGENTS.md")).read()
        and "AGENTS.md" in open(os.path.join(ROOT, "CLAUDE.md")).read(),
        "AGENTS.md is the canonical provider-neutral guide and Claude discovers it",
    )
    repo_url = manifest.get("repository", {}).get("url", "")
    check(
        "github.com/NeotaskInc/neomax-orchestrator" in repo_url,
        "package metadata points to the organization-owned public repository",
    )
    check(
        not os.path.exists(os.path.join(ROOT, ".no-mistakes.yaml")),
        "stale private test-runner configuration is not published",
    )
    entrypoints = []
    for name in os.listdir(BIN):
        path = os.path.join(BIN, name)
        if os.path.isfile(path) and "." not in name:
            entrypoints.append(path)
    entrypoints.append(os.path.join(ROOT, "project", "neomax-worktrees"))
    check(
        all(
            (
                open(path, "rb").read(2) == b"#!" and os.access(path, os.X_OK)
                for path in entrypoints
            )
        ),
        "extensionless command files have shebangs and execute bits",
    )
    try:
        raw = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                cwd=ROOT,
            )
            .decode()
            .split("\x00")
        )
        files = [
            os.path.join(ROOT, path)
            for path in raw
            if path and os.path.isfile(os.path.join(ROOT, path))
        ]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [name for name in dirs if name != ".git"]
            files.extend((os.path.join(base, name) for name in names))
    private_path = re.compile(
        b"(?:/Users/|/home/)[A-Za-z0-9._-]+(?:/|\\b)|[A-Za-z]:\\\\\\\\Users\\\\\\\\[A-Za-z0-9._-]+(?:\\\\\\\\|\\b)"
    )
    denied = [
        value.strip().lower().encode()
        for value in os.environ.get("NEOMAX_PRIVACY_DENYLIST", "").split(",")
        if value.strip()
    ]
    leaks = []
    for path in files:
        try:
            body = open(path, "rb").read()
        except OSError:
            continue
        if private_path.search(body) or any(
            (value in body.lower() for value in denied)
        ):
            leaks.append(os.path.relpath(path, ROOT))
    check(
        not leaks,
        "tracked product files contain no user-home paths or local denylist values",
    )
    bad_brand = b"Neo" + b"Max"
    check(
        not any((bad_brand in open(path, "rb").read() for path in files)),
        "product branding is consistently Neomax, never camel-cased",
    )
    retired_global_names = (
        b"C" + b"MAX_",
        b"c" + b"max-portal",
        b"c" + b"max-usage-agent",
        b"c" + b"max-worktrees",
        b"c" + b"max-aliases",
        b"c" + b"max-ci",
        b"c" + b"max-issue",
    )
    stale = []
    for path in files:
        try:
            body = open(path, "rb").read()
        except OSError:
            continue
        if any((name in body for name in retired_global_names)):
            stale.append(os.path.relpath(path, ROOT))
    check(
        not stale,
        "shared product surfaces use neomax/NEOMAX names; cmax is Claude-only",
    )


def test_provider_workflow_parity_and_install():
    print("test_provider_workflow_parity_and_install")
    root = ROOT
    names = ("neomax", "rotate", "find-issues", "fix-issues")
    for name in names:
        paths = [
            os.path.join(root, "claude", "commands", name + ".md"),
            os.path.join(root, "codex", "prompts", name + ".md"),
            os.path.join(root, "opencode", "commands", name + ".md"),
            os.path.join(root, "grok", "commands", name + ".md"),
            os.path.join(root, "kimi", "skills", name, "SKILL.md"),
        ]
        check(
            all((os.path.isfile(path) for path in paths)),
            name + " is tracked for all five providers",
        )
        bodies = [open(path).read() for path in paths[:4]]
        if name == "rotate":
            for engine, body in zip(("claude", "codex", "opencode", "grok"), bodies):
                check(
                    "neomax rotate --engine " + engine in body,
                    engine + " /rotate targets its own provider",
                )
        else:
            check(
                len(set(bodies)) == 1,
                name + " workflow stays identical across markdown providers",
            )
        check(
            "name: " + name in open(paths[4]).read(), name + " has a named Kimi skill"
        )
    with tempfile.TemporaryDirectory() as td:
        home = os.path.join(td, "home")
        fakebin = os.path.join(td, "bin")
        os.makedirs(fakebin)
        for command in ("claude", "codex", "opencode", "kimi", "grok"):
            path = os.path.join(fakebin, command)
            with open(path, "w") as f:
                f.write("#!/bin/sh\nprintf 'fixture 1.0\\n'\n")
            os.chmod(path, 493)
        uname = os.path.join(fakebin, "uname")
        with open(uname, "w") as f:
            f.write("#!/bin/sh\nprintf 'Linux\\n'\n")
        os.chmod(uname, 493)
        for rel in (
            ".claude/commands",
            ".codex",
            ".kimi-code",
            ".kimi-code-acct2",
            ".grok",
            ".grok-acct2",
            ".local/bin",
        ):
            os.makedirs(os.path.join(home, rel), exist_ok=True)
        os.makedirs(os.path.join(home, ".kimi-code", "credentials"), exist_ok=True)
        with open(
            os.path.join(home, ".kimi-code", "credentials", "kimi-code.json"), "w"
        ) as f:
            json.dump({"access_token": "fixture-not-real"}, f)
        legacy = "cde" + "legate"
        legacy_command = "dele" + "gate"
        legacy_global = "c" + "max"
        os.symlink(
            os.path.join(root, "bin", "neomax"),
            os.path.join(home, ".local", "bin", legacy),
        )
        for old in (
            legacy_global + "-portal",
            legacy_global + "-usage-agent",
            legacy_global + "-worktrees",
            legacy_global + "-aliases.zsh",
        ):
            os.symlink(
                os.path.join(root, "bin", "neomax"),
                os.path.join(home, ".local", "bin", old),
            )
        os.symlink(
            os.path.join(root, "claude", "commands", "neomax.md"),
            os.path.join(home, ".claude", "commands", legacy_command + ".md"),
        )
        legacy_state = os.path.join(home, "." + legacy)
        os.makedirs(legacy_state)
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": os.path.join(home, ".local", "bin", legacy)
                                + " ls",
                            }
                        ],
                    }
                ]
            }
        }
        with open(os.path.join(home, ".claude", "settings.json"), "w") as f:
            json.dump(settings, f)
        env = dict(
            os.environ,
            HOME=home,
            PATH=fakebin + os.pathsep + os.environ.get("PATH", ""),
            NEOMAX_CLAUDE_ACCOUNTS="2",
            NEOMAX_CODEX_ACCOUNTS="2",
        )
        result = subprocess.run(
            ["bash", os.path.join(root, "install.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(
            result.returncode == 0,
            "installer wires provider workflows in a hermetic home",
        )
        installed = [
            os.path.join(home, ".claude", "commands", "neomax.md"),
            os.path.join(home, ".claude", "commands", "rotate.md"),
            os.path.join(home, ".codex-acct2", "prompts", "rotate.md"),
            os.path.join(home, ".codex-acct2", "prompts", "fix-issues.md"),
            os.path.join(home, ".config", "opencode", "commands", "rotate.md"),
            os.path.join(home, ".config", "opencode", "commands", "find-issues.md"),
            os.path.join(home, ".kimi-code-acct2", "skills", "rotate", "SKILL.md"),
            os.path.join(home, ".kimi-code-acct2", "skills", "neomax", "SKILL.md"),
            os.path.join(home, ".grok-acct2", "commands", "rotate.md"),
            os.path.join(home, ".grok-acct2", "commands", "fix-issues.md"),
        ]
        check(
            all((os.path.exists(path) for path in installed)),
            "all provider/profile workflow links resolve",
        )
        universal_bins = (
            "neomax-portal",
            "neomax-usage-agent",
            "neomax-worktrees",
            "neomax-aliases.zsh",
        )
        check(
            all(
                (
                    os.path.islink(os.path.join(home, ".local", "bin", name))
                    for name in universal_bins
                )
            )
            and all(
                (
                    not os.path.lexists(os.path.join(home, ".local", "bin", name))
                    for name in (
                        legacy_global + "-portal",
                        legacy_global + "-usage-agent",
                        legacy_global + "-worktrees",
                        legacy_global + "-aliases.zsh",
                    )
                )
            ),
            "installer exposes Neomax universal tools and removes retired global names",
        )
        check(
            not os.path.lexists(os.path.join(home, ".local", "bin", legacy))
            and (
                not os.path.lexists(
                    os.path.join(home, ".claude", "commands", legacy_command + ".md")
                )
            ),
            "installer removes the retired executable and Claude command",
        )
        check(
            os.path.islink(os.path.join(home, ".neomax"))
            and os.path.realpath(os.path.join(home, ".neomax"))
            == os.path.realpath(legacy_state),
            "installer preserves legacy run state behind the new path",
        )
        installed_settings = json.load(
            open(os.path.join(home, ".claude", "settings.json"))
        )
        commands = [
            h.get("command", "")
            for groups in installed_settings.get("hooks", {}).values()
            for group in groups
            for h in group.get("hooks", [])
        ]
        check(
            all((legacy not in command for command in commands))
            and sum(("neomax" in command for command in commands)) == 4,
            "installer migrates lifecycle hooks to neomax",
        )
        for helper, provider, profile, child in (
            ("kmx", "kimi", ".kimi-code-acct3", "skills/rotate/SKILL.md"),
            ("gmx", "grok", ".grok-acct3", "commands/rotate.md"),
        ):
            helper_env = dict(env)
            helper_env["NEOMAX_" + provider.upper() + "_BIN"] = os.path.join(
                fakebin, provider
            )
            run = subprocess.run(
                ["zsh", os.path.join(BIN, helper), "models", "3"],
                env=helper_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            check(
                run.returncode == 0
                and os.path.exists(os.path.join(home, profile, child)),
                helper + " installs workflows into newly created profiles",
            )
        launch_env = dict(
            env, NEOMAX_DRY_RUN="1", NEOMAX_HOME=os.path.join(home, ".neomax")
        )
        run = subprocess.run(
            [
                "zsh",
                os.path.join(root, "bin", "neomax"),
                "--engine",
                "kimi",
                "fixture task",
            ],
            env=launch_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(
            run.returncode == 0
            and "kimi orchestrator" in run.stdout
            and ("workers=kimi" in run.stdout)
            and ("DRY:" in run.stdout),
            "neomax launches the only connected provider without a model request",
        )


def test_neomax_engine_selection():
    print("test_neomax_engine_selection")
    saved = {
        name: getattr(m, name)
        for name in (
            "STATE_DIR",
            "pick_orch_account",
            "fetch_engine_usage",
            "live_counts",
        )
    }
    with tempfile.TemporaryDirectory() as td:
        try:
            m.STATE_DIR = td
            profiles = {engine: os.path.join(td, engine) for engine in m.ENGINES}
            m.pick_orch_account = lambda engine="claude": profiles.get(engine)
            m.live_counts = lambda engine: {profiles[engine]: 0}
            pressure = {"claude": 70.0, "codex": 25.0}
            m.fetch_engine_usage = lambda profile, engine: (
                {
                    "five_hour": {
                        "used_percent": pressure[engine] if engine == "claude" else None
                    },
                    "seven_day": {"used_percent": pressure[engine]},
                }
                if engine in pressure
                else None
            )
            choice = m.pick_neomax_orchestrator(cwd=td)
            check(
                choice["engine"] == "codex"
                and set(choice["engines"]) == set(m.ENGINES),
                "Neomax chooses the healthy provider with the most measured headroom",
            )
            pressure.update({"claude": 95.0, "codex": 95.0})
            choice = m.pick_neomax_orchestrator(
                priority="kimi,claude,codex,opencode,grok", cwd=td
            )
            check(
                choice["engine"] == "kimi" and choice["pressure"] is None,
                "reactive-only provider wins before known near-wall providers without invented usage",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                m.cmd_pick_neomax(
                    ["--json", "--record", "--engine", "kimi", "--cwd", td]
                )
            recorded = json.loads(output.getvalue())
            check(
                recorded["engine"] == "kimi"
                and m._neomax_selection_state()["projects"][os.path.abspath(td)][
                    "engine"
                ]
                == "kimi",
                "Neomax records the selected provider per project",
            )
            pressure.update({"claude": 1.0, "codex": 2.0})
            choice = m.pick_neomax_orchestrator(cwd=td, resume=True)
            check(
                choice["engine"] == "kimi",
                "Neomax resume reuses the project's eligible provider",
            )
        finally:
            for name, value in saved.items():
                setattr(m, name, value)


def test_goal_construction():
    print("test_goal_construction")
    ca = m.claude_args(
        {
            "engine": "claude",
            "_prompt_to_send": "Do the work.",
            "goal": "all tests pass",
            "max_turns": 12,
            "session": "s",
        }
    )
    check(ca[-1] == "/goal all tests pass\n\nDo the work.", "claude prepends /goal")
    check(
        "--max-turns" in ca and ca[ca.index("--max-turns") + 1] == "12",
        "claude --max-turns flag",
    )
    xa = m.codex_args(
        {
            "engine": "codex",
            "_prompt_to_send": "Do the work.",
            "goal": "build is green",
            "max_turns": 12,
            "workdir": "/tmp/wt",
            "model": "gpt-5.5",
            "effort": "high",
        }
    )
    p = xa[-1]
    check("/goal" not in p, "codex never emits a /goal slash command")
    check("OBJECTIVE" in p and "build is green" in p, "codex injects objective block")
    check(p.startswith(m.WORKER_DIRECTIVE), "codex order: directive first")
    check("at most 12 rounds" in p, "codex folds max_turns as advisory cap")
    check(
        m.claude_args({"engine": "claude", "_prompt_to_send": "X.", "session": "s"})[-1]
        == "X.",
        "claude no-goal prompt unchanged",
    )
    xn = m.codex_args(
        {
            "engine": "codex",
            "_prompt_to_send": "X.",
            "workdir": "/tmp/wt",
            "model": "gpt-5.5",
            "effort": "high",
        }
    )[-1]
    check("OBJECTIVE" not in xn and xn.endswith("X."), "codex no-goal prompt unchanged")


def test_validation():
    print("test_validation")
    with tempfile.TemporaryDirectory() as state:
        env = _env(state, state + "/p1", state + "/p2")
        cases = [
            (["--goal", "   ", "auto", "x"], "non-empty"),
            (["--goal", "ok", "--max-turns", "0", "auto", "x"], "positive integer"),
            (["--goal", "ok", "--max-turns", "abc", "auto", "x"], "positive integer"),
            (["--max-turns", "5", "auto", "x"], "requires --goal"),
            (["--goal", "x" * 4001, "auto", "x"], "too long"),
        ]
        for argv, needle in cases:
            r = subprocess.run(
                [sys.executable, NEOMAX] + argv, env=env, capture_output=True, text=True
            )
            check(
                r.returncode == 2 and needle in r.stderr,
                "reject %s (exit 2, '%s')" % (argv[:2], needle),
            )


def test_delegation_brief_check():
    """The soft delegation-brief gate: warns on a thin/vague worker prompt (the
    'fix the latency issue' case), stays silent on a complete brief or with --brief,
    and never blocks (it only warns)."""
    print("test_delegation_brief_check")
    import io, contextlib, sys as _sys

    def warns(prompt, **kw):
        buf = io.StringIO()
        old = _sys.stderr
        _sys.stderr = buf
        try:
            m.delegation_brief_check(prompt, **kw)
        finally:
            _sys.stderr = old
        return "THIN DELEGATION" in buf.getvalue()

    check(warns("Fix the latency issue with X and Y"), "vague one-liner -> warns")
    check(
        warns(
            "Please make everything faster and better across the whole app as much as you can do it well"
        ),
        "long-but-vague (no path, no acceptance) -> warns",
    )
    check(
        not warns(
            "Refactor src/api/list.ts to remove the N+1; add a test asserting one DB round-trip; npm test must pass; do not touch the schema."
        ),
        "complete brief (path + acceptance) -> silent",
    )
    check(
        not warns("anything at all", brief_ok=True),
        "--brief acknowledges a trivial task -> silent",
    )
    check(
        not warns("Edit src/x.ts line 40 to guard null; add a test that it passes"),
        "short but scoped + verified -> silent",
    )
    m.delegation_brief_check("x")
    check(True, "brief check only warns, never blocks")


def test_delegation_gate():
    """neomax DELEGATION is ORCHESTRATOR-ONLY: require_orchestrator REFUSES a plain `claude` or
    `cmax solo` session (no NEOMAX_ROLE) but allows an orchestrator (NEOMAX_ROLE set), a delegated worker
    (NEOMAX_WORKER), or an explicit NEOMAX_ALLOW_DELEGATE=1 override."""
    print("test_delegation_gate")
    import io, contextlib

    keys = ("NEOMAX_ROLE", "NEOMAX_MODE", "NEOMAX_WORKER", "NEOMAX_ALLOW_DELEGATE")
    saved = {k: os.environ.get(k) for k in keys}

    def gate():
        for k in keys:
            os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v
        rc = "allowed"
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                m.require_orchestrator("auto")
            except SystemExit as e:
                rc = e.code
        return rc

    try:
        env = {}
        check(
            gate() == 2, "plain `claude` session (no NEOMAX_ROLE) -> delegation REFUSED"
        )
        env = {"NEOMAX_MODE": "solo"}
        check(gate() == 2, "`cmax solo` session -> delegation REFUSED")
        env = {"NEOMAX_ROLE": "claude"}
        check(gate() == "allowed", "orchestrator session (NEOMAX_ROLE) -> allowed")
        env = {"NEOMAX_WORKER": "1"}
        check(
            gate() == "allowed",
            "a delegated worker (NEOMAX_WORKER) -> allowed (re-dispatch)",
        )
        env = {"NEOMAX_ALLOW_DELEGATE": "1"}
        check(
            gate() == "allowed", "explicit NEOMAX_ALLOW_DELEGATE=1 override -> allowed"
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
