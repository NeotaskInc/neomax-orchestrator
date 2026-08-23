"""Codex models, fast mode, live usage, children, and weekly-window policy."""

from .support import *


def test_codex_fast_mode():
    """Every codex spawn pins fast mode (service_tier=fast) explicitly via -c, so it never
    depends on a per-profile config.toml — alongside the model + reasoning-effort overrides.
    Mirrors how the model is pinned (drift-proof)."""
    print("test_codex_fast_mode")
    check(
        m.CODEX_SERVICE_TIER == "fast", "fast mode constant is the 'fast' service tier"
    )

    def cflags(args):
        return [args[i + 1] for (i, a) in enumerate(args) if a == "-c"]

    xa = m.codex_args(
        {
            "engine": "codex",
            "_prompt_to_send": "work",
            "workdir": "/tmp/wt",
            "model": "gpt-5.5",
            "effort": "high",
        }
    )
    cv = cflags(xa)
    check("service_tier=fast" in cv, "codex worker pins service_tier=fast")
    check(
        "model_reasoning_effort=high" in cv, "codex worker still pins reasoning effort"
    )
    check(
        "-m" in xa and xa[xa.index("-m") + 1] == "gpt-5.5",
        "codex worker still pins gpt-5.5",
    )
    xu = m.codex_args(
        {
            "engine": "codex",
            "_prompt_to_send": "deep",
            "workdir": "/tmp/wt",
            "model": "gpt-5.5",
            "effort": "xhigh",
        }
    )
    cvu = cflags(xu)
    check(
        "service_tier=fast" in cvu and "model_reasoning_effort=xhigh" in cvu,
        "xhigh codex worker keeps fast mode + xhigh reasoning",
    )
    xp = m.codex_args(
        {
            "engine": "codex",
            "_prompt_to_send": "scout",
            "workdir": "/tmp/wt",
            "model": "gpt-5.5",
            "effort": "high",
            "plan_mode": True,
        }
    )
    check(
        "service_tier=fast" in cflags(xp),
        "plan-mode codex scout pins service_tier=fast",
    )


def test_codex_children_kinds():
    """Codex stream items: tool/command items are kind 'step' (live while running, but NOT
    sub-agents); collab/agent items are kind 'agent' (count as sub-agents). item.started
    marks running; item.completed resolves; a terminal turn closes stragglers."""
    print("test_codex_children_kinds")
    with tempfile.TemporaryDirectory() as st:
        log = os.path.join(st, "w.jsonl")
        with open(log, "w") as f:
            f.write(json.dumps({"type": "thread.started", "thread_id": "t1"}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "item.started",
                        "item": {
                            "id": "c1",
                            "type": "command_execution",
                            "command": "npm test",
                        },
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "item.started",
                        "item": {"id": "a1", "type": "collab_agent", "name": "helper"},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "c1",
                            "type": "command_execution",
                            "command": "npm test",
                            "exit_code": 0,
                        },
                    }
                )
                + "\n"
            )
        ev = m.parse_codex_events(log)
        kinds = {c["id"]: (c["kind"], c["status"]) for c in ev["children"]}
        check(
            kinds["c1"] == ("step", "completed"),
            "tool item: kind step, resolved by item.completed",
        )
        check(
            kinds["a1"] == ("agent", "running"),
            "collab item: kind agent, running until completion",
        )
        active_agents = [
            c
            for c in ev["children"]
            if c["status"] == "running" and c.get("kind", "agent") == "agent"
        ]
        check(
            len(active_agents) == 1,
            "only agent-kind running children count toward sub-agents",
        )
        with open(log, "a") as f:
            f.write(json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
        ev2 = m.parse_codex_events(log)
        check(
            all((c["status"] != "running" for c in ev2["children"])),
            "terminal turn.completed closes any still-running children",
        )


def test_codex_usage_live():
    print("test_codex_usage_live")
    import time

    with tempfile.TemporaryDirectory() as st:
        prof = os.path.join(st, ".codex")
        sess = os.path.join(prof, "sessions", "2026", "06", "11")
        os.makedirs(sess)
        roll = os.path.join(sess, "rollout-x.jsonl")
        with open(roll, "w") as f:
            f.write('{"type":"session_meta"}\n')
            f.write(
                json.dumps(
                    {
                        "type": "token_count",
                        "rate_limits": {
                            "primary": {
                                "used_percent": 2.0,
                                "resets_at": int(time.time()) + 3600,
                            },
                            "secondary": {
                                "used_percent": 10.0,
                                "resets_at": int(time.time()) + 99999,
                            },
                        },
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "token_count",
                        "rate_limits": {
                            "primary": {
                                "used_percent": 5.0,
                                "resets_at": int(time.time()) + 3600,
                            },
                            "secondary": {
                                "used_percent": 24.0,
                                "resets_at": int(time.time()) + 99999,
                            },
                        },
                    }
                )
                + "\n"
            )
        w = m.read_codex_window(prof)
        check(
            w
            and w["five_hour"]["used_percent"] == 5.0
            and (w["seven_day"]["used_percent"] == 24.0),
            "read_codex_window tail-reads the LATEST token_count (live rollout)",
        )
        saved = m.USAGE_DIR
        try:
            m.USAGE_DIR = os.path.join(st, "usage")
            u = m.fetch_codex_usage(prof)
            check(
                u and u["five_hour"]["used_percent"] == 5.0 and u.get("observed_at"),
                "fetch_codex_usage returns the live rollout reading + caches it",
            )
            check(
                m.weekly_window(prof, "codex") == 24.0,
                "weekly_window uses the live codex reading",
            )
            check(
                m.usage_window(prof, "codex") == 0.0,
                "usage_window is 0.0 for codex — no 5h window to gate on",
            )
        finally:
            m.USAGE_DIR = saved


def test_codex_weekly_only_rotation():
    """Codex removed its 5-hour limit: its rate_limits event now carries one
    window (window_minutes=10080 = 7 days) with `secondary: null`. Three things must hold:
      1. windows are classified by DECLARED LENGTH, never by primary/secondary position — the old
         positional mapping reported the WEEKLY number as a phantom 5h reading and left weekly
         unknown, silently breaking rotation, the weekly hard wall, and selection skipping;
      2. every 5h gate is INERT for Codex (usage_window == 0.0 at the source), so no stale cache
         can resurrect a phantom 5h number — Codex rotates on WEEKLY >= 99% only;
      3. a PRE-change cache (real weekly usage stored under five_hour) is migrated, not discarded —
         otherwise an exhausted account reads as fresh and keeps receiving work.
    Claude is unaffected: 5h >= 99% OR weekly >= 99%."""
    print("test_codex_weekly_only_rotation")

    def rl_line(rl):
        return json.dumps(
            {
                "payload": {
                    "type": "token_count",
                    "rate_limits": rl,
                    "info": {"total_token_usage": {}},
                }
            }
        )

    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, ".codex")
        sess = os.path.join(prof, "sessions", "2026", "07", "27")
        os.makedirs(sess)

        def write(rl):
            path = os.path.join(sess, "rollout-x.jsonl")
            with open(path, "w") as f:
                f.write(rl_line(rl) + "\n")
            return path

        write(
            {
                "primary": {
                    "used_percent": 37.0,
                    "window_minutes": 10080,
                    "resets_at": 2000000000,
                },
                "secondary": None,
            }
        )
        w = m.read_codex_window(prof)
        check(
            w["seven_day"]["used_percent"] == 37.0,
            "new one-window shape: primary(10080m) classified as WEEKLY",
        )
        check(
            w["five_hour"]["used_percent"] is None,
            "new one-window shape: no phantom 5h reading",
        )
        write(
            {
                "primary": {
                    "used_percent": 60.0,
                    "window_minutes": 300,
                    "resets_at": 2000000000,
                },
                "secondary": {
                    "used_percent": 12.0,
                    "window_minutes": 10080,
                    "resets_at": 2000000001,
                },
            }
        )
        w = m.read_codex_window(prof)
        check(
            w["five_hour"]["used_percent"] == 60.0
            and w["seven_day"]["used_percent"] == 12.0,
            "old two-window shape: 300m -> 5h, 10080m -> weekly",
        )
        write(
            {
                "primary": {"used_percent": 71.0, "resets_at": 2000000000},
                "secondary": {"used_percent": 9.0, "resets_at": 2000000001},
            }
        )
        w = m.read_codex_window(prof)
        check(
            w["five_hour"]["used_percent"] == 71.0
            and w["seven_day"]["used_percent"] == 9.0,
            "no window_minutes -> legacy positional mapping",
        )
        write({"primary": None, "secondary": None})
        w = m.read_codex_window(prof)
        check(
            w["five_hour"]["used_percent"] is None
            and w["seven_day"]["used_percent"] is None,
            "empty rate_limits -> both windows unknown (no crash)",
        )
    check(
        m.engine_has_5h("claude") is True and m.engine_has_5h("codex") is False,
        "Claude has a 5h window; Codex does not",
    )
    saved = m.fetch_codex_usage
    try:
        m.fetch_codex_usage = lambda p: {
            "five_hour": {"used_percent": 100.0, "resets_at": None},
            "seven_day": {"used_percent": 4.0, "resets_at": None},
        }
        check(
            m.usage_window("/x", "codex") == 0.0,
            "codex 5h reads 0.0 at the SOURCE (a stale phantom 5h can't gate anything)",
        )
        check(m.weekly_window("/x", "codex") == 4.0, "codex weekly still reads through")
    finally:
        m.fetch_codex_usage = saved
    check(
        m.ORCH_5H_ROTATE == 99.0
        and m.ORCH_7D_ROTATE == 99.0
        and (m.SOLO_5H_ROTATE == 99.0)
        and (m.SOLO_7D_ROTATE == 99.0),
        "every rotation threshold is 99% (5h and weekly, orchestrator and solo)",
    )
    for five, seven, want in (
        (99.0, 0.0, True),
        (100.0, 0.0, True),
        (0.0, 99.0, True),
        (98.9, 98.9, False),
        (0.0, 0.0, False),
    ):
        got = m.rotate_advice(five, seven, "claude")[0]
        check(
            got is want, "claude 5h=%.1f weekly=%.1f -> rotate=%s" % (five, seven, want)
        )
    for five, seven, want in (
        (100.0, 0.0, False),
        (99.0, 98.9, False),
        (0.0, 99.0, True),
        (0.0, 100.0, True),
        (0.0, 98.9, False),
    ):
        got = m.rotate_advice(five, seven, "codex")[0]
        check(
            got is want,
            "codex 5h=%.1f weekly=%.1f -> rotate=%s (weekly-only)"
            % (five, seven, want),
        )
    check(
        "no 5h window" in m.rotate_advice(0.0, 10.0, "codex")[1],
        "codex 'headroom ok' reason says WHY 5h is ignored",
    )
    check(
        "5h" in m.rotate_advice(99.5, 0.0, "claude")[1],
        "claude reason names the 5h trigger",
    )
    old = {
        "five_hour": {"used_percent": 100.0, "resets_at": 2000000000},
        "seven_day": {"used_percent": None, "resets_at": None},
        "observed_at": 1,
    }
    mig = m._normalize_codex_windows(old)
    check(
        mig["seven_day"]["used_percent"] == 100.0
        and mig["five_hour"]["used_percent"] is None,
        "pre-change cache: the real weekly number is moved out of five_hour",
    )
    check(mig["seven_day"]["resets_at"] == 2000000000, "migration keeps the reset time")
    check(m._normalize_codex_windows(mig) == mig, "migration is idempotent")
    good = {
        "five_hour": {"used_percent": None, "resets_at": None},
        "seven_day": {"used_percent": 37.0, "resets_at": 2000000000},
    }
    check(
        m._normalize_codex_windows(good) == good,
        "a correctly-shaped record is untouched",
    )
    check(m._normalize_codex_windows(None) is None, "garbage in -> no crash")
    saved_w = m.weekly_window
    try:
        m.weekly_window = lambda p, e: 100.0 if "acct3" in p else 0.0
        check(
            m.weekly_window("/x/.codex-acct3", "codex") >= m.SEVEN_D_HARD,
            "a migrated 100%-weekly account is at/over the weekly hard wall (selection skips it)",
        )
    finally:
        m.weekly_window = saved_w


def test_codex_model_family():
    """Codex keeps the gpt-5.6 defaults/aliases and accepts other explicit CLI models."""
    print("test_codex_model_family")
    check(
        m.CODEX_MODEL == "gpt-5.6-sol",
        "Codex default worker model is gpt-5.6-sol (flagship)",
    )
    check(
        m.resolve_codex_model(None) == "gpt-5.6-sol",
        "no --codex-model → the sol default",
    )
    check(
        m.resolve_codex_model("terra") == "gpt-5.6-terra"
        and m.resolve_codex_model("luna") == "gpt-5.6-luna"
        and (m.resolve_codex_model("SOL") == "gpt-5.6-sol"),
        "tier nicknames resolve (case-insensitive)",
    )
    check(
        m.resolve_codex_model("gpt-5.6") == "gpt-5.6-sol",
        "the bare gpt-5.6 alias is pinned to the explicit sol slug",
    )
    check(
        m.resolve_codex_model("gpt-5.6-luna") == "gpt-5.6-luna",
        "a full family slug passes through",
    )
    check(
        m.codex_model_tier("gpt-5.6-terra") == "terra"
        and m.codex_model_tier("gpt-5.5") is None,
        "codex_model_tier maps a family slug back to its nickname, None otherwise",
    )
    for custom in ("gpt-5.5", "gpt-4o", "custom-codex"):
        check(
            m.resolve_codex_model(custom) == custom,
            "resolve_codex_model passes explicit supported model %r" % custom,
        )
    check(
        m.resolve_kimi_model("custom-kimi") == "custom-kimi"
        and m.resolve_grok_model("custom-grok") == "custom-grok",
        "Kimi and Grok accept explicit CLI models while retaining their defaults",
    )
    for tier, slug in m.CODEX_MODELS.items():
        check(
            m.model_pricing(slug)["in"] > 0 and m.model_pricing(slug)["out"] > 0,
            "gpt-5.6-%s is priced" % tier,
        )
        p = m.model_pricing(slug)
        check(
            p["cw"] == 0 and abs(p["cr"] - p["in"] * 0.1) < 1e-09,
            "gpt-5.6-%s cache rates follow the OpenAI multipliers" % tier,
        )
    meta = '{"payload":{"type":"session_meta","model":"gpt-5.5"}}'
    check(
        m.codex_model_in_line(meta) == "gpt-5.5",
        "the rollout header's model is sniffed",
    )
    check(
        m.codex_model_in_line('{"payload":{"type":"token_count"}}') is None,
        "a line with no model yields None",
    )
    tc = '{"payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":100,"cached_input_tokens":10,"output_tokens":5}}}}'
    check(
        m._codex_record_from_line(tc, ".codex", "s1", "gpt-5.5")["model"] == "gpt-5.5",
        "a sniffed model is carried onto the usage record",
    )
    check(
        m._codex_record_from_line(tc, ".codex", "s1")["model"] == m.CODEX_MODEL,
        "no sniffed model → the current Codex default",
    )
