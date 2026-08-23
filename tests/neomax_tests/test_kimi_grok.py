"""Kimi Code and Grok models, authentication, launchers, events, and telemetry."""

from .support import *


def test_kimi_models_events_and_telemetry():
    """Kimi coverage uses only stream/session fixtures; no CLI or network request."""
    print("test_kimi_models_events_and_telemetry")
    check(
        m.KIMI_MODEL == "kimi-code/k3"
        and m.resolve_kimi_model("k2.7") == "kimi-code/kimi-for-coding",
        "K3 is default/main and K2.7 resolves to the managed coding model",
    )
    rec = {
        "engine": "kimi",
        "_prompt_to_send": "Do the work.",
        "goal": "tests pass",
        "max_turns": 3,
        "workdir": "/tmp/wt",
        "model": m.KIMI_MODEL,
    }
    args = m.kimi_args(rec, resume_session="session_resume")
    check(
        args[args.index("-m") + 1] == m.KIMI_MODEL
        and "--auto" not in args
        and (args[args.index("-S") + 1] == "session_resume")
        and ("OBJECTIVE" in args[-1]),
        "Kimi worker pins K3, uses prompt-mode auto, supports resume, and receives the goal block",
    )
    plan_args = m.kimi_args(dict(rec, plan_mode=True))
    check(
        "--plan" not in plan_args and "--auto" not in plan_args,
        "Kimi prompt mode avoids mutually exclusive permission flags",
    )
    check(
        "READ-ONLY PLAN SCOUT" in plan_args[-1],
        "Kimi planning prompt carries the read-only scout contract",
    )
    with tempfile.TemporaryDirectory() as td:
        saved_state = m.STATE_DIR
        try:
            m.STATE_DIR = os.path.join(td, "state")
            plan_profile = os.path.join(td, "plan-profile")
            os.makedirs(os.path.join(plan_profile, "credentials"))
            os.makedirs(os.path.join(plan_profile, "sessions"))
            with open(os.path.join(plan_profile, "config.toml"), "w") as f:
                f.write(
                    '[models."kimi-code/k3"]\nprovider = "kimi-code"\nmax_context_size = 262144\n\n[tools]\ndisabled = ["Read"]\n'
                )
            plan_home = m.kimi_plan_home({"profile": plan_profile})
            config = open(os.path.join(plan_home, "config.toml")).read()
            check(
                os.path.islink(os.path.join(plan_home, "credentials"))
                and os.path.islink(os.path.join(plan_home, "sessions"))
                and ('[models."kimi-code/k3"]' in config)
                and ('"Read"' in config)
                and ('"Write"' not in config)
                and ('"Bash"' not in config),
                "Kimi plan profile keeps model/OAuth/session state but exposes read-only tools only",
            )
        finally:
            m.STATE_DIR = saved_state
        stream = os.path.join(td, "kimi.jsonl")
        rows = [
            {
                "role": "assistant",
                "content": "working",
                "tool_calls": [{"id": "call_1", "function": {"name": "Agent"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "done"},
            {"role": "assistant", "content": "complete"},
            {
                "role": "meta",
                "type": "session.resume_hint",
                "session_id": "session_kimi",
            },
        ]
        with open(stream, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        ev = m.parse_kimi_events(stream)
        check(
            ev["subtype"] == "success"
            and ev["session_id"] == "session_kimi"
            and (ev["result_text"] == "complete"),
            "Kimi stream captures success/session/final text",
        )
        check(
            ev["children"][0]["kind"] == "agent"
            and ev["children"][0]["status"] == "completed",
            "Kimi native agent tool is tracked as a completed subagent",
        )
        limited = os.path.join(td, "limited.jsonl")
        before = time.time()
        with open(limited, "w") as f:
            f.write(
                json.dumps(
                    {
                        "role": "error",
                        "error": {
                            "message": "rate limit",
                            "statusCode": 429,
                            "responseHeaders": {"retry-after": "120"},
                        },
                    }
                )
                + "\n"
            )
        limited_ev = m.parse_kimi_events(limited)
        check(
            limited_ev["rate_limited"]
            and before + 115 < limited_ev["resets_at"] < before + 125,
            "Kimi structured 429 stream records its exact retry window",
        )
        profile = os.path.join(td, ".kimi-code-acct2")
        cred = os.path.join(profile, "credentials")
        os.makedirs(cred)
        with open(os.path.join(cred, "kimi-code.json"), "w") as f:
            json.dump({"refresh_token": "fixture-not-real"}, f)
        check(
            m.logged_in(profile, "kimi"), "isolated Kimi credential profile is detected"
        )
        api_profile = os.path.join(td, ".kimi-code-api")
        os.makedirs(api_profile)
        with open(os.path.join(api_profile, "config.toml"), "w") as f:
            f.write('api_key = "fixture-not-real"\n')
        check(
            m.kimi_auth_method(api_profile) == "API key"
            and (not m.logged_in(api_profile, "kimi")),
            "Kimi API-key profiles are recognized but managed K3 workers require OAuth",
        )
        sdir = os.path.join(profile, "sessions", "fixture-session")
        for aid in ("main", "agent-0"):
            os.makedirs(os.path.join(sdir, "agents", aid))
        state = {
            "sessionId": "session_fixture",
            "workDir": "/repo",
            "title": "Build feature",
            "createdAt": int((time.time() - 60) * 1000),
            "agents": {
                "main": {"type": "main"},
                "agent-0": {"type": "sub", "parentAgentId": "main"},
            },
        }
        with open(os.path.join(sdir, "state.json"), "w") as f:
            json.dump(state, f)
        with open(os.path.join(profile, "session_index.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "session_fixture",
                        "sessionDir": sdir,
                        "workDir": "/repo",
                    }
                )
                + "\n"
            )
        now_ms = int(time.time() * 1000)

        def wire(aid, inp, out):
            path = os.path.join(sdir, "agents", aid, "wire.jsonl")
            with open(path, "w") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "usage.record",
                            "model": m.KIMI_MODEL,
                            "usage": {
                                "inputOther": inp,
                                "output": out,
                                "inputCacheRead": 7,
                                "inputCacheCreation": 2,
                            },
                            "time": now_ms,
                        }
                    )
                    + "\n"
                )
                f.write(
                    json.dumps(
                        {
                            "type": "context.append_loop_event",
                            "event": {
                                "type": "tool.call",
                                "args": {"file_path": "/repo/src/a.py"},
                            },
                        }
                    )
                    + "\n"
                )

        wire("main", 100, 40)
        wire("agent-0", 50, 20)
        snap = m.kimi_profile_snapshot(profile, 7)
        t = snap["totals"]
        check(
            t["in"] == 150 and t["out"] == 60 and (t["cr"] == 14) and (t["cw"] == 4),
            "Kimi snapshot totals exact input/output/cache usage across agents",
        )
        check(
            t["sessions"] == 1
            and t["native_subagents"] == 1
            and (t["tool_calls"] == 2)
            and (t["files"] == 1),
            "Kimi snapshot captures sessions, subagents, tools, and files",
        )


def test_grok_models_events_telemetry_and_launchers():
    """Grok Build coverage uses auth/event/session fixtures and launcher dry-runs only."""
    print("test_grok_models_events_telemetry_and_launchers")
    check(
        m.GROK_MODEL == "grok-4.6",
        "Grok workers pin the authenticated catalog's coding model",
    )
    rec = {
        "engine": "grok",
        "_prompt_to_send": "Do the work.",
        "goal": "tests pass",
        "max_turns": 4,
        "workdir": "/tmp/wt",
    }
    args = m.grok_args(rec, resume_session="session_resume")
    check(
        args[0] == m.GROK_BIN
        and args[args.index("--model") + 1] == m.GROK_MODEL
        and (args[args.index("--resume") + 1] == "session_resume")
        and (args[args.index("--max-turns") + 1] == "4")
        and ("--always-approve" in args)
        and ("OBJECTIVE" in args[-1]),
        "Grok worker pins model, resume, max turns, approvals, and goal",
    )
    check(
        "--permission-mode" in m.grok_args(dict(rec, plan_mode=True))
        and "--always-approve" not in m.grok_args(dict(rec, plan_mode=True)),
        "Grok planning scouts use the read-only permission mode",
    )
    with tempfile.TemporaryDirectory() as td:
        stream = os.path.join(td, "grok.jsonl")
        rows = [
            {
                "type": "tool_call",
                "toolCallId": "call_1",
                "toolName": "spawn_subagent",
                "title": "Explore",
                "status": "in_progress",
            },
            {"type": "tool_call_update", "toolCallId": "call_1", "status": "completed"},
            {"type": "text", "data": "GROK_OK"},
            {
                "type": "usage",
                "usage": {
                    "input_tokens": 90,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 10,
                },
            },
            {
                "type": "end",
                "stopReason": "end_turn",
                "sessionId": "session_grok",
                "usage": {
                    "input_tokens": 90,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 10,
                },
            },
        ]
        with open(stream, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        ev = m.parse_grok_events(stream)
        check(
            ev["subtype"] == "success"
            and ev["session_id"] == "session_grok"
            and (ev["result_text"] == "GROK_OK")
            and (ev["usage"]["output_tokens"] == 20),
            "Grok streaming JSON captures success, session, response, and usage",
        )
        check(
            ev["children"][0]["kind"] == "agent"
            and ev["children"][0]["status"] == "completed",
            "Grok native subagent tool is tracked to completion",
        )
        limited = os.path.join(td, "limited.jsonl")
        with open(limited, "w") as f:
            f.write(
                json.dumps({"type": "error", "message": "429 rate limit exceeded"})
                + "\n"
            )
        limited_ev = m.parse_grok_events(limited)
        check(
            limited_ev["rate_limited"] and limited_ev["api_error_status"] == 429,
            "Grok error stream classifies a provider 429",
        )
        home = os.path.join(td, "home")
        profile = os.path.join(home, ".grok")
        os.makedirs(profile)
        with open(os.path.join(profile, "auth.json"), "w") as f:
            json.dump(
                {"xai::oidc": {"key": "fixture-not-real", "auth_mode": "oidc"}}, f
            )
        check(
            m.logged_in(profile, "grok"), "isolated Grok OAuth profile auth is detected"
        )
        sdir = os.path.join(profile, "sessions", "repo", "session-grok")
        os.makedirs(sdir)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        summary = {
            "info": {"id": "session-grok", "cwd": "/repo"},
            "created_at": stamp,
            "updated_at": stamp,
            "current_model_id": m.GROK_MODEL,
            "generated_title": "Build feature",
        }
        with open(os.path.join(sdir, "summary.json"), "w") as f:
            json.dump(summary, f)
        updates = [
            {"sessionUpdate": "tool_call", "rawInput": {"path": "/repo/src/a.py"}},
            {
                "sessionUpdate": "subagent_spawned",
                "subagent_id": "sub-1",
                "child_session_id": "child-1",
                "subagent_type": "explore",
            },
            {
                "sessionUpdate": "subagent_finished",
                "subagent_id": "sub-1",
                "child_session_id": "child-1",
                "status": "completed",
                "tool_calls": 2,
            },
            {
                "sessionUpdate": "turn_completed",
                "prompt_id": "prompt-1",
                "stop_reason": "end_turn",
                "usage": {
                    "inputTokens": 120,
                    "outputTokens": 40,
                    "cachedReadTokens": 20,
                    "cacheCreationTokens": 5,
                    "reasoningTokens": 8,
                    "modelCalls": 2,
                    "modelUsage": {m.GROK_MODEL: {"modelCalls": 2}},
                },
            },
        ]
        with open(os.path.join(sdir, "updates.jsonl"), "w") as f:
            for update in updates:
                f.write(
                    json.dumps({"timestamp": stamp, "params": {"update": update}})
                    + "\n"
                )
        snap = m.grok_profile_snapshot(profile, 7)
        t = snap["totals"]
        check(
            t["in"] == 95
            and t["out"] == 40
            and (t["reasoning"] == 8)
            and (t["cr"] == 20)
            and (t["cw"] == 5)
            and (t["requests"] == 2),
            "Grok snapshot records exact input/output/reasoning/cache usage",
        )
        check(
            t["sessions"] == 1
            and t["main_sessions"] == 1
            and (t["native_subagents"] == 1)
            and (t["tool_calls"] == 1)
            and (t["files"] == 1),
            "Grok snapshot captures sessions, subagents, tools, and files",
        )
        env = dict(
            os.environ,
            HOME=home,
            NEOMAX_DRY_RUN="1",
            NEOMAX_HOME=os.path.join(td, "state"),
            NEOMAX_GROK_PROFILES=profile,
            PATH=BIN + os.pathsep + os.environ.get("PATH", ""),
        )
        for script, argv, label in (
            ("gmx", ["run", "1"], "gmx account launcher"),
            ("gmax", ["1"], "gmax orchestrator launcher"),
        ):
            r = subprocess.run(
                ["zsh", os.path.join(BIN, script)] + argv,
                env=env,
                capture_output=True,
                text=True,
                cwd=td,
                timeout=15,
            )
            check(
                r.returncode == 0 and "DRY:" in r.stdout and (m.GROK_MODEL in r.stdout),
                label + " dry-runs with the default Grok Build model",
            )
        for script, argv, label in (
            ("gmx", ["run", "1", "--model", "custom-grok"], "gmx"),
            ("gmax", ["1", "--model", "custom-grok"], "gmax"),
        ):
            r = subprocess.run(
                ["zsh", os.path.join(BIN, script)] + argv,
                env=env,
                capture_output=True,
                text=True,
                cwd=td,
                timeout=15,
            )
            check(
                r.returncode == 0 and "custom-grok" in r.stdout,
                label + " passes an explicit Grok model without a model request",
            )
