"""OpenCode model policy, launchers, event parsing, and local telemetry."""

from .support import *


def test_opencode_ox_alpha_policy_and_events():
    """Hermetic OX coverage: command/config/event fixtures only; never starts OpenCode."""
    print("test_opencode_ox_alpha_policy_and_events")
    check(
        m.OPENCODE_MODEL == "opencode-go/ox-alpha-free",
        "OpenCode model is the explicit OX Alpha catalog entry",
    )
    policy = json.loads(m.opencode_policy_content())
    pins = [policy["model"], policy["small_model"]] + [
        v["model"] for v in policy["agent"].values()
    ]
    check(
        policy["enabled_providers"] == ["opencode-go"],
        "OX policy allows only OpenCode Go",
    )
    check(
        set(policy["agent"])
        == {
            "build",
            "plan",
            "general",
            "explore",
            "scout",
            "compaction",
            "title",
            "summary",
        },
        "OX policy covers every built-in/native-agent role",
    )
    check(
        all((x == m.OPENCODE_MODEL for x in pins)),
        "primary/small/native agents all pin exact OX Alpha",
    )
    check(
        "x-preview" not in json.dumps(policy)
        and "big-pickle" not in json.dumps(policy),
        "policy contains neither the Zen alias nor Big Pickle",
    )
    custom = "opencode/big-pickle"
    custom_policy = json.loads(m.opencode_policy_content(custom))
    custom_pins = [custom_policy["model"], custom_policy["small_model"]] + [
        v["model"] for v in custom_policy["agent"].values()
    ]
    check(
        custom_policy["enabled_providers"] == ["opencode"]
        and all((x == custom for x in custom_pins))
        and (custom_policy["share"] == "disabled"),
        "OpenCode override repins primary/small/native agents to a local-registry model",
    )
    rec = {
        "engine": "opencode",
        "_prompt_to_send": "Do the work.",
        "goal": "tests pass",
        "max_turns": 4,
        "workdir": "/tmp/wt",
    }
    args = m.opencode_args(rec, resume_session="ses_resume")
    check(
        args[:2] == [m.OPENCODE_BIN, "run"]
        and args[args.index("--model") + 1] == m.OPENCODE_MODEL,
        "OpenCode worker command pins exact OX Alpha",
    )
    check(
        args[args.index("--session") + 1] == "ses_resume" and "OBJECTIVE" in args[-1],
        "OpenCode worker supports session resume and goal block",
    )
    custom_args = m.opencode_args(dict(rec, model=custom))
    check(
        custom_args[custom_args.index("--model") + 1] == custom,
        "OpenCode worker passes an explicit local-registry model",
    )
    with tempfile.TemporaryDirectory() as td:
        good = os.path.join(td, "good.jsonl")
        rows = [
            {"type": "step_start", "sessionID": "ses_ox"},
            {
                "type": "tool_use",
                "sessionID": "ses_ox",
                "part": {
                    "tool": "task",
                    "callID": "call_1",
                    "state": {"status": "running", "title": "Explore repo"},
                },
            },
            {"type": "text", "sessionID": "ses_ox", "part": {"text": "done"}},
            {
                "type": "step_finish",
                "sessionID": "ses_ox",
                "part": {
                    "reason": "stop",
                    "cost": 0,
                    "tokens": {
                        "total": 30,
                        "input": 20,
                        "output": 7,
                        "reasoning": 3,
                        "cache": {"write": 0, "read": 8},
                    },
                },
            },
        ]
        with open(good, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        ev = m.parse_opencode_events(good)
        check(
            ev["subtype"] == "success"
            and ev["session_id"] == "ses_ox"
            and (ev["result_text"] == "done"),
            "OpenCode success stream captures terminal state/session/text",
        )
        check(
            ev["usage"]["total"] == 30 and ev["usage"]["cache_read"] == 8,
            "OpenCode step_finish tokens are recorded",
        )
        check(
            ev["children"][0]["kind"] == "agent"
            and ev["children"][0]["status"] == "completed",
            "OpenCode native task tool is tracked as a subagent",
        )
        limited = os.path.join(td, "limited.jsonl")
        reset = time.time() + 90
        row = {
            "type": "error",
            "sessionID": "ses_limit",
            "error": {
                "name": "APIError",
                "data": {
                    "message": "Too many requests",
                    "statusCode": "429",
                    "responseHeaders": {"retry-after": "90"},
                    "responseBody": "rate limit",
                },
            },
        }
        with open(limited, "w") as f:
            f.write(json.dumps(row) + "\n")
        ev = m.parse_opencode_events(limited)
        check(
            ev["rate_limited"] and str(ev["api_error_status"]) == "429",
            "OpenCode structured numeric/string 429 is classified as a rate limit",
        )
        check(
            abs(ev["resets_at"] - reset) < 3,
            "OpenCode Retry-After becomes an account cooldown reset",
        )
        profile = os.path.join(td, ".opencode-acct2")
        os.makedirs(os.path.join(profile, "opencode"))
        with open(os.path.join(profile, "opencode", "auth.json"), "w") as f:
            json.dump({"opencode-go": {"type": "api", "key": "fixture-not-real"}}, f)
        check(
            m.logged_in(profile, "opencode"),
            "isolated OpenCode Go profile auth is detected",
        )
        with open(os.path.join(profile, "opencode", "auth.json"), "w") as f:
            json.dump({"opencode": {"type": "api", "key": "fixture-not-real"}}, f)
        check(
            m.logged_in(profile, "opencode"),
            "OpenCode selection accepts any authenticated registry provider",
        )
        link = os.path.join(td, "neomax-link")
        os.symlink(NEOMAX, link)
        link_loader = SourceFileLoader("neomax_via_symlink", link)
        link_spec = importlib.util.spec_from_loader("neomax_via_symlink", link_loader)
        linked = importlib.util.module_from_spec(link_spec)
        link_loader.exec_module(linked)
        check(
            linked.NEOMAX_REPO_DIR == os.path.dirname(HERE)
            and json.loads(linked.opencode_policy_content())["model"]
            == m.OPENCODE_MODEL,
            "installed neomax symlink resolves the checkout's OpenCode model policy",
        )


def test_opencode_launchers_dry_run():
    """End-to-end launcher wiring with a dummy local auth fixture; OpenCode is never exec'd."""
    print("test_opencode_launchers_dry_run")
    with tempfile.TemporaryDirectory() as td:
        home = os.path.join(td, "home")
        auth_dir = os.path.join(home, ".local", "share", "opencode")
        os.makedirs(auth_dir)
        with open(os.path.join(auth_dir, "auth.json"), "w") as f:
            json.dump({"opencode-go": {"type": "api", "key": "fixture-not-real"}}, f)
        kimi_profile = os.path.join(home, ".kimi-code")
        os.makedirs(os.path.join(kimi_profile, "credentials"))
        with open(
            os.path.join(kimi_profile, "credentials", "kimi-code.json"), "w"
        ) as f:
            json.dump({"access_token": "fixture-not-real"}, f)
        claude_profile = os.path.join(home, ".claude")
        codex_profile = os.path.join(home, ".codex")
        os.makedirs(claude_profile)
        os.makedirs(codex_profile)
        with open(os.path.join(claude_profile, ".credentials.json"), "w") as f:
            json.dump({"fixture": True}, f)
        with open(os.path.join(codex_profile, "auth.json"), "w") as f:
            json.dump({"fixture": True}, f)
        env = dict(
            os.environ,
            HOME=home,
            NEOMAX_DRY_RUN="1",
            NEOMAX_HOME=os.path.join(td, "state"),
            NEOMAX_PROFILES=claude_profile,
            NEOMAX_CODEX_PROFILES=codex_profile,
            NEOMAX_OPENCODE_PROFILES=os.path.join(home, ".opencode"),
            NEOMAX_KIMI_PROFILES=kimi_profile,
            PATH=BIN + os.pathsep + os.environ.get("PATH", ""),
        )
        for script, args, label in (
            (os.path.join(BIN, "ocx"), ["run", "1"], "ocx account launcher"),
            (os.path.join(BIN, "ocmax"), ["1"], "ocmax orchestrator launcher"),
        ):
            r = subprocess.run(
                ["zsh", script] + args,
                env=env,
                capture_output=True,
                text=True,
                cwd=td,
                timeout=15,
            )
            check(
                r.returncode == 0
                and "DRY:" in r.stdout
                and (m.OPENCODE_MODEL in r.stdout),
                label + " dry-runs with exact OX Alpha (no model request)",
            )
        for script, args, label in (
            (
                os.path.join(BIN, "ocx"),
                ["run", "1", "--model", "opencode/big-pickle"],
                "ocx",
            ),
            (
                os.path.join(BIN, "ocmax"),
                ["1", "--model", "opencode/big-pickle"],
                "ocmax",
            ),
        ):
            r = subprocess.run(
                ["zsh", script] + args,
                env=env,
                capture_output=True,
                text=True,
                cwd=td,
                timeout=15,
            )
            check(
                r.returncode == 0 and "opencode/big-pickle" in r.stdout,
                label + " accepts an explicit registry model without a model request",
            )
        for script, args, model in (
            ("cmax", ["1", "--model", "custom-claude"], "custom-claude"),
            ("cdxmax", ["1", "--model", "custom-codex"], "custom-codex"),
            ("cdx", ["run", "1", "--model", "custom-codex"], "custom-codex"),
        ):
            r = subprocess.run(
                ["zsh", os.path.join(BIN, script)] + args,
                env=env,
                capture_output=True,
                text=True,
                cwd=td,
                timeout=15,
            )
            check(
                r.returncode == 0 and model in r.stdout,
                script
                + " passes an explicit orchestrator model without a model request",
            )
        r = subprocess.run(
            ["zsh", os.path.join(BIN, "kmax"), "1"],
            env=env,
            capture_output=True,
            text=True,
            cwd=td,
            timeout=15,
        )
        check(
            r.returncode == 0
            and "DRY:" in r.stdout
            and (m.KIMI_MODEL in r.stdout)
            and ("--auto --output-format" not in r.stdout),
            "kmax dry-runs K3 bootstrap/resume without a model request",
        )
        r = subprocess.run(
            ["zsh", os.path.join(BIN, "kmx"), "run", "1"],
            env=env,
            capture_output=True,
            text=True,
            cwd=td,
            timeout=15,
        )
        check(
            r.returncode == 0 and "DRY:" in r.stdout and (m.KIMI_MODEL in r.stdout),
            "kmx account launcher dry-runs K3 without a model request",
        )
        for script, args, label in (
            ("kmax", ["1", "--model", "custom-kimi"], "kmax"),
            ("kmx", ["run", "1", "--model", "custom-kimi"], "kmx"),
        ):
            r = subprocess.run(
                ["zsh", os.path.join(BIN, script)] + args,
                env=env,
                capture_output=True,
                text=True,
                cwd=td,
                timeout=15,
            )
            check(
                r.returncode == 0 and "custom-kimi" in r.stdout,
                label + " passes an explicit Kimi model without a model request",
            )
        projects = json.load(open(os.path.join(td, "state", "projects.json")))
        auto = next(iter(projects.values()))
        check(
            len(projects) == 1
            and os.path.realpath(auto["root"]) == os.path.realpath(td),
            "orchestrator launcher automatically registers its current directory as the project",
        )
        fake_portal = os.path.join(td, "fake-portal")
        with open(fake_portal, "w") as f:
            f.write("#!/bin/sh\nprintf 'UNIVERSAL-PORTAL:%s\\n' \"$*\"\n")
        os.chmod(fake_portal, 493)
        portal_env = dict(env, NEOMAX_PORTAL_BIN=fake_portal)
        for launcher in ("cmax", "cdxmax", "ocmax", "kmax", "gmax"):
            r = subprocess.run(
                ["zsh", os.path.join(BIN, launcher), "portal", "9191"],
                env=portal_env,
                capture_output=True,
                text=True,
                cwd=td,
                timeout=15,
            )
            check(
                r.returncode == 0 and r.stdout.strip() == "UNIVERSAL-PORTAL:9191",
                launcher + " portal executes the same universal portal with its port",
            )
        r = subprocess.run(
            ["zsh", os.path.join(BIN, "ocmax"), "--workers", "all", "portal", "9192"],
            env=portal_env,
            capture_output=True,
            text=True,
            cwd=td,
            timeout=15,
        )
        check(
            r.returncode == 0 and r.stdout.strip() == "UNIVERSAL-PORTAL:9192",
            "ocmax portal is recognized after launcher flags and never starts OpenCode",
        )


def test_opencode_local_telemetry():
    """OpenCode portal data comes only from a hermetic SQLite fixture; no CLI/network/model."""
    print("test_opencode_local_telemetry")
    import sqlite3

    with tempfile.TemporaryDirectory() as td:
        profile = os.path.join(td, ".opencode-acct2")
        data_dir = os.path.join(profile, "opencode")
        os.makedirs(data_dir)
        db = os.path.join(data_dir, "opencode.db")
        cx = sqlite3.connect(db)
        cx.executescript(
            "\n        CREATE TABLE session (\n          id text PRIMARY KEY, project_id text, parent_id text, directory text, title text,\n          agent text, model text, time_created integer, time_updated integer, time_archived integer,\n          summary_additions integer, summary_deletions integer, summary_files integer,\n          tokens_input integer, tokens_output integer, tokens_reasoning integer,\n          tokens_cache_read integer, tokens_cache_write integer, cost real);\n        CREATE TABLE message (id text PRIMARY KEY, session_id text, time_created integer,\n                              time_updated integer, data text);\n        CREATE TABLE part (id text PRIMARY KEY, message_id text, session_id text,\n                           time_created integer, time_updated integer, data text);\n        "
        )
        now = int(time.time() * 1000)
        model = json.dumps({"providerID": "opencode-go", "id": "ox-alpha-free"})
        sessions = [
            (
                "ses_parent",
                "p",
                None,
                "/repo",
                "Build the portal",
                "build",
                model,
                now - 5000,
                now,
                None,
                0,
                0,
                0,
                100,
                200,
                30,
                400,
                0,
                0.0,
            ),
            (
                "ses_child",
                "p",
                "ses_parent",
                "/repo",
                "Audit telemetry (@general subagent)",
                "general",
                model,
                now - 4000,
                now,
                None,
                0,
                0,
                0,
                50,
                70,
                10,
                80,
                0,
                0.0,
            ),
        ]
        cx.executemany(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            sessions,
        )

        def assistant(
            mid,
            sid,
            provider,
            model_id,
            tokens,
            completed=True,
            error=None,
            agent="build",
        ):
            timing = {"created": now - 3000}
            if completed:
                timing["completed"] = now - 2000
            data = {
                "role": "assistant",
                "providerID": provider,
                "modelID": model_id,
                "agent": agent,
                "time": timing,
                "tokens": tokens,
                "cost": 0,
            }
            if error:
                data["error"] = error
            cx.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                (mid, sid, now - 3000, now, json.dumps(data)),
            )

        assistant(
            "msg_parent",
            "ses_parent",
            "opencode-go",
            "ox-alpha-free",
            {
                "input": 100,
                "output": 200,
                "reasoning": 30,
                "cache": {"read": 400, "write": 0},
            },
        )
        assistant(
            "msg_child",
            "ses_child",
            "opencode-go",
            "ox-alpha-free",
            {
                "input": 50,
                "output": 70,
                "reasoning": 10,
                "cache": {"read": 80, "write": 0},
            },
            agent="general",
        )
        assistant(
            "msg_429",
            "ses_parent",
            "opencode-go",
            "ox-alpha-free",
            {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            error={
                "name": "APIError",
                "data": {"statusCode": 429, "message": "Too many requests"},
            },
        )
        assistant(
            "msg_open",
            "ses_parent",
            "opencode-go",
            "ox-alpha-free",
            {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            completed=False,
        )
        parts = [
            (
                "pt1",
                "msg_parent",
                "ses_parent",
                {
                    "type": "tool",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "/repo/a.py"},
                    },
                },
            ),
            (
                "pt2",
                "msg_parent",
                "ses_parent",
                {
                    "type": "tool",
                    "tool": "task",
                    "state": {"status": "completed", "input": {"description": "audit"}},
                },
            ),
            (
                "pt3",
                "msg_child",
                "ses_child",
                {
                    "type": "tool",
                    "tool": "edit",
                    "state": {
                        "status": "completed",
                        "input": {
                            "filePath": "/repo/a.py",
                            "oldString": "old\nline",
                            "newString": "new\nline\nplus",
                        },
                    },
                },
            ),
            (
                "pt4",
                "msg_child",
                "ses_child",
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {"status": "running", "input": {"command": "test"}},
                },
            ),
        ]
        cx.executemany(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            [
                (i, mid, sid, now - 1000, now, json.dumps(data))
                for (i, mid, sid, data) in parts
            ],
        )
        cx.commit()
        cx.close()
        snap = m.opencode_profile_snapshot(profile, 7)
        t = snap["totals"]
        check(
            snap["available"]
            and t["out"] == 270
            and (t["in"] == 150)
            and (t["reasoning"] == 40)
            and (t["cr"] == 480),
            "SQLite snapshot captures exact OpenCode input/output/reasoning/cache tokens",
        )
        check(
            t["requests"] == 4
            and t["completions"] == 2
            and (t["unfinished"] == 1)
            and (t["errors"] == 1)
            and (t["rate_limits"] == 1),
            "snapshot distinguishes requests/completions/unfinished/errors/structured 429s",
        )
        check(
            t["sessions"] == 2
            and t["main_sessions"] == 1
            and (t["native_subagents"] == 1)
            and (t["tool_calls"] == 4)
            and (t["files"] == 1),
            "snapshot captures parent/child sessions, tools, and edited files",
        )
        child = next((s for s in snap["sessions"] if s["id"] == "ses_child"))
        check(
            child["active"]
            and child["files"][0]["adds"] == 3
            and (child["files"][0]["dels"] == 2),
            "running native child and persisted Edit file/line activity are recovered",
        )
        check(
            snap["models"][0]["model"] == m.OPENCODE_MODEL
            and snap["agents"][0]["agent"] in ("build", "general"),
            "exact OpenCode model and built-in/native agent-role breakdowns are retained",
        )
        saved_env = {
            k: os.environ.get(k)
            for k in (
                "NEOMAX_PROFILES",
                "NEOMAX_CODEX_PROFILES",
                "NEOMAX_OPENCODE_PROFILES",
            )
        }
        saved = (m.USAGE_LEDGER_DIR, m.all_runs, m.orchestrators_on_account)
        try:
            os.environ["NEOMAX_PROFILES"] = os.path.join(td, "no-claude")
            os.environ["NEOMAX_CODEX_PROFILES"] = os.path.join(td, "no-codex")
            os.environ["NEOMAX_OPENCODE_PROFILES"] = profile
            m.USAGE_LEDGER_DIR = os.path.join(td, "ledger")
            os.makedirs(m.USAGE_LEDGER_DIR)
            m.all_runs = lambda: []
            m.orchestrators_on_account = lambda *a, **k: []
            usage = m.gather_usage(7)
            oc = next((r for r in usage["by_provider"] if r["provider"] == "opencode"))
            check(
                oc["out"] == 270
                and oc["reasoning"] == 40
                and (oc["cost"] == 0)
                and (oc["requests"] == 4)
                and (oc["rate_limits"] == 1),
                "unified usage API includes exact OpenCode usage at its recorded $0 cost",
            )
            sess = m.gather_sessions(3, 20)
            subs = m.gather_subagent_history(3, 20)
            check(
                len(sess) == 1
                and sess[0]["engine"] == "opencode"
                and (sess[0]["session"] == "ses_parent")
                and sess[0]["active"],
                "interactive session history includes the OpenCode parent session",
            )
            check(
                len(subs) == 1
                and subs[0]["engine"] == "opencode"
                and (subs[0]["agent"] == "ses_child")
                and (subs[0]["tokens"]["out"] == 70),
                "subagent history includes the OpenCode native child and its own tokens",
            )
        finally:
            (m.USAGE_LEDGER_DIR, m.all_runs, m.orchestrators_on_account) = saved
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
