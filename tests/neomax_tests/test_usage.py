"""Quota fetching, usage ledgers, pricing, backoff, watchers, and keepalive."""

from .support import *


def test_fetch_claude_usage():
    """Per-account usage via the OAuth usage API — hermetic (token + HTTP stubbed).
    This is the fix for the shared .ratelimit.json cycling/wrong-account bug:
    every profile is queried with ITS OWN token and cached per account."""
    print("test_fetch_claude_usage")
    import datetime, time

    orig = (m.claude_oauth_token, m.http_get_json, m.USAGE_DIR)
    try:
        with tempfile.TemporaryDirectory() as st:
            m.USAGE_DIR = os.path.join(st, "usage")
            calls = []
            iso = "2099-01-01T09:19:59.500000+00:00"

            def fake_http(pct):

                def go(url, headers, timeout):
                    calls.append((url, headers))
                    return {
                        "five_hour": {"utilization": pct, "resets_at": iso},
                        "seven_day": {"utilization": 29.0, "resets_at": None},
                    }

                return go

            m.claude_oauth_token = lambda p: "sk-test-token"
            m.http_get_json = fake_http(48.0)
            w = m.fetch_claude_usage("/h/.claude-acct2")
            check(
                w and w["source"] == "claude-api", "fetch returns a claude-api window"
            )
            check(w["five_hour"]["used_percent"] == 48.0, "5h %% from API utilization")
            exp = datetime.datetime.fromisoformat(iso).timestamp()
            check(
                abs(w["five_hour"]["resets_at"] - exp) < 1,
                "ISO resets_at -> epoch seconds",
            )
            check(
                w["seven_day"]["used_percent"] == 29.0
                and w["seven_day"]["resets_at"] is None,
                "7d window carried, null reset tolerated",
            )
            (url, hdr) = calls[0]
            check(url == m.CLAUDE_USAGE_URL, "calls the oauth usage endpoint")
            check(
                hdr["Authorization"] == "Bearer sk-test-token"
                and hdr["anthropic-beta"] == "oauth-2025-04-20"
                and (hdr["anthropic-version"] == "2023-06-01"),
                "sends bearer token + oauth beta headers",
            )
            m.http_get_json = fake_http(99.0)
            w2 = m.fetch_claude_usage("/h/.claude-acct2")
            check(
                w2["five_hour"]["used_percent"] == 48.0 and len(calls) == 1,
                "<60s cache reused (no API hammering)",
            )
            m.http_get_json = fake_http(100.0)
            w3 = m.fetch_claude_usage("/h/.claude")
            check(
                w3["five_hour"]["used_percent"] == 100.0 and len(calls) == 2,
                "per-account cache: other profile fetches its OWN usage",
            )

            def boom(url, headers, timeout):
                raise RuntimeError("401")

            m.http_get_json = boom
            check(
                m.fetch_claude_usage("/h/.claude-acct3") is None,
                "HTTP failure + no cache -> None (honest unknown)",
            )
            import json as _json

            stale_cache = os.path.join(m.USAGE_DIR, "claude-.claude-acct2.json")
            _json.dump(
                {
                    "five_hour": {"used_percent": 42, "resets_at": time.time() + 3600},
                    "seven_day": {"used_percent": 71, "resets_at": None},
                    "source": "claude-api",
                    "observed_at": int(time.time()) - 120,
                },
                open(stale_cache, "w"),
            )
            m.claude_oauth_token = lambda p: "sk-test-token"
            m.http_get_json = boom
            sw = m.fetch_claude_usage("/h/.claude-acct2")
            check(
                sw and sw["five_hour"]["used_percent"] == 42 and (not sw.get("stale")),
                "transient failure serves a RECENT cached reading WITHOUT the stale label (a seconds-old reading through one failed poll isn't 'last known')",
            )
            _json.dump(
                {
                    "five_hour": {"used_percent": 42, "resets_at": time.time() + 3600},
                    "seven_day": {"used_percent": 71, "resets_at": None},
                    "source": "claude-api",
                    "observed_at": int(time.time()) - (m.USAGE_STALE_LABEL_S + 60),
                },
                open(stale_cache, "w"),
            )
            sw2 = m.fetch_claude_usage("/h/.claude-acct2")
            check(
                sw2 and sw2.get("stale") is True,
                "genuinely old fallback (> USAGE_STALE_LABEL_S) IS labeled 'last known'",
            )
            _json.dump(
                {
                    "five_hour": {"used_percent": 42},
                    "seven_day": {"used_percent": 71},
                    "source": "claude-api",
                    "observed_at": int(time.time()) - (m.USAGE_STALE_OK_S + 600),
                },
                open(stale_cache, "w"),
            )
            check(
                m.fetch_claude_usage("/h/.claude-acct2") is None,
                "ancient cache not served (> stale-OK window) -> None",
            )
            m.claude_oauth_token = lambda p: None
            n0 = len(calls)
            check(
                m.fetch_claude_usage("/h/.claude-acct4") is None and len(calls) == n0,
                "no valid token -> None, no HTTP call",
            )
            os.makedirs(m.USAGE_DIR, exist_ok=True)
            with open(os.path.join(m.USAGE_DIR, "claude-.claude-acct5.json"), "w") as f:
                json.dump(
                    {
                        "five_hour": {"used_percent": 5},
                        "observed_at": int(time.time()),
                        "source": "claude-statusline",
                    },
                    f,
                )
            m.claude_oauth_token = lambda p: "sk-test-token"
            m.http_get_json = fake_http(77.0)
            w5 = m.fetch_claude_usage("/h/.claude-acct5")
            check(
                w5["five_hour"]["used_percent"] == 77.0,
                "legacy statusline cache blob ignored (refetched from API)",
            )
            prof = os.path.join(st, ".claude-accttest")
            os.makedirs(prof)
            cred = os.path.join(prof, ".credentials.json")
            with open(cred, "w") as f:
                json.dump(
                    {
                        "claudeAiOauth": {
                            "accessToken": "sk-old",
                            "expiresAt": int((time.time() - 10) * 1000),
                        }
                    },
                    f,
                )
            check(orig[0](prof) is None, "expired token skipped (would 401)")
            with open(cred, "w") as f:
                json.dump(
                    {
                        "claudeAiOauth": {
                            "accessToken": "sk-new",
                            "expiresAt": int((time.time() + 3600) * 1000),
                        }
                    },
                    f,
                )
            check(
                orig[0](prof) == "sk-new",
                "valid .credentials.json token used (ms expiry ok)",
            )
            (m.fetch_claude_usage, keep) = (
                lambda p: {
                    "five_hour": {"used_percent": 83.0, "resets_at": time.time() + 600}
                },
                m.fetch_claude_usage,
            )
            check(
                m.usage_window("/h/.claude", "claude") == 83.0,
                "usage_window(claude) uses fetch_claude_usage",
            )
            m.fetch_claude_usage = lambda p: None
            check(
                m.usage_window("/h/.claude", "claude") == 0.0,
                "unknown usage -> 0.0 (treated fresh) for selection",
            )
            m.fetch_claude_usage = keep
    finally:
        (m.claude_oauth_token, m.http_get_json, m.USAGE_DIR) = orig


def test_keepalive():
    print("test_keepalive")
    import time

    saved = (
        m.engine_profiles,
        m.logged_in,
        m.live_counts,
        m.keepalive_account,
        m._claude_token_expiry,
        m._codex_token_expiry,
    )
    try:
        profs = ["/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3"]
        m.engine_profiles = lambda e: profs if e == "claude" else []
        m.logged_in = lambda p, e="claude": True
        now = time.time()
        m.live_counts = lambda e: {profs[0]: 2, profs[1]: 0, profs[2]: 0}
        exp = {profs[0]: now + 60, profs[1]: now + 300, profs[2]: now + 99999}
        m._claude_token_expiry = lambda p: exp[p]
        m._codex_token_expiry = lambda p: None
        fired = []
        m.keepalive_account = lambda p, e: (fired.append(p), True)[1]
        state = {}
        n = m.keepalive_pass(state)
        check(
            fired == [profs[0], profs[1]],
            "keepalive refreshes near-expiry accounts INCLUDING the live orchestrator account",
        )
        check(n == 2, "two near-expiry accounts refreshed (live + idle)")
        check(profs[2] not in fired, "a still-valid account is not force-refreshed")
        fired.clear()
        m.keepalive_pass(state)
        check(fired == [], "rate-limited: not re-attempted immediately")
    finally:
        (
            m.engine_profiles,
            m.logged_in,
            m.live_counts,
            m.keepalive_account,
            m._claude_token_expiry,
            m._codex_token_expiry,
        ) = saved


def test_usage_tracking():
    print("test_usage_tracking")
    check(
        m.model_pricing("claude-fable-5")["in"] == 10.0, "fable-5 input rate $10/Mtok"
    )
    check(
        m.model_pricing("claude-opus-4-8")["out"] == 25.0,
        "opus-4-8 output rate $25/Mtok",
    )
    check(m.model_pricing("gpt-5.5")["out"] == 30.0, "gpt-5.5 output rate $30/Mtok")
    check(m.model_pricing("claude-fable-5[1m]")["in"] == 10.0, "tolerates [1m] suffix")
    check(
        m.model_pricing("totally-unknown")["in"] == m.PRICING_DEFAULT["in"],
        "unknown model -> default rates",
    )
    check(
        abs(m.usage_cost("claude-fable-5", 0, 1000000, 0, 0) - 50.0) < 1e-06,
        "1M fable output = $50",
    )
    check(
        abs(m.usage_cost("claude-opus-4-8", 1000000, 0, 0, 1000000) - (5.0 + 0.5))
        < 1e-06,
        "opus 1M input + 1M cache-read = $5.50",
    )
    cl = '{"uuid":"u1","message":{"role":"assistant","model":"claude-fable-5","usage":{"input_tokens":100,"output_tokens":20,"cache_creation_input_tokens":5,"cache_read_input_tokens":3}}}'
    r = m._claude_records_from_line(cl, ".claude")
    check(
        r
        and r["id"] == "u1"
        and (r["in"] == 100)
        and (r["out"] == 20)
        and (r["cw"] == 5)
        and (r["cr"] == 3)
        and (r["kind"] == "add"),
        "claude line -> add record with per-completion tokens",
    )
    check(
        m._claude_records_from_line('{"message":{"role":"user"}}', ".claude") is None,
        "non-assistant line ignored",
    )
    cx = '{"type":"token_count","info":{"total_token_usage":{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":50}}}'
    r2 = m._codex_record_from_line(cx, ".codex", "sessA")
    check(
        r2
        and r2["id"] == "sessA"
        and (r2["in"] == 600)
        and (r2["cr"] == 400)
        and (r2["out"] == 50)
        and (r2["kind"] == "total"),
        "codex line -> total record; in = total input - cached",
    )
    saved = m.USAGE_LEDGER_DIR
    try:
        with tempfile.TemporaryDirectory() as st:
            m.USAGE_LEDGER_DIR = st
            day = __import__("time").strftime("%Y-%m-%d")
            recs = [
                {
                    "ts": int(__import__("time").time()),
                    "provider": "claude",
                    "account": ".claude",
                    "model": "claude-fable-5",
                    "id": "u1",
                    "kind": "add",
                    "in": 100,
                    "out": 20,
                    "cw": 0,
                    "cr": 0,
                },
                {
                    "ts": int(__import__("time").time()),
                    "provider": "claude",
                    "account": ".claude",
                    "model": "claude-fable-5",
                    "id": "u1",
                    "kind": "add",
                    "in": 100,
                    "out": 20,
                    "cw": 0,
                    "cr": 0,
                },
                {
                    "ts": int(__import__("time").time()),
                    "provider": "codex",
                    "account": ".codex",
                    "model": "gpt-5.5",
                    "id": "sA",
                    "kind": "total",
                    "in": 500,
                    "out": 30,
                    "cw": 0,
                    "cr": 100,
                },
                {
                    "ts": int(__import__("time").time()),
                    "provider": "codex",
                    "account": ".codex",
                    "model": "gpt-5.5",
                    "id": "sA",
                    "kind": "total",
                    "in": 800,
                    "out": 50,
                    "cw": 0,
                    "cr": 200,
                },
            ]
            with open(os.path.join(st, day + ".jsonl"), "w") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")
            dd = m._read_usage_ledger(0)
            check(
                len(dd) == 2,
                "dedup: 4 lines -> 2 records (add first-wins, total max-wins)",
            )
            u = m.gather_usage(0)
            cl_in = next((r for r in u["by_provider"] if r["provider"] == "claude"))[
                "in"
            ]
            cx_in = next((r for r in u["by_provider"] if r["provider"] == "codex"))[
                "in"
            ]
            check(cl_in == 100, "claude add counted once (dedup)")
            check(cx_in == 800, "codex total takes the LATEST/largest, not the sum")
            check(u["grand"]["completions"] == 2, "grand completions = 2")
    finally:
        m.USAGE_LEDGER_DIR = saved


def test_billing_id_dedup():
    """A multi-block assistant turn is written as SEVERAL transcript lines (thinking / text /
    tool_use), each with the SAME API message.id but a DIFFERENT line uuid, each repeating the
    full message-level usage. Dedup MUST key on message.id (the billed call) so the turn counts
    ONCE — keying on the line uuid over-counts every multi-block turn 2-3x (the cache-read
    inflation bug)."""
    print("test_billing_id_dedup")
    base = '{"timestamp":"2026-06-01T00:00:00Z","sessionId":"s","requestId":"req_1","uuid":"%s","message":{"id":"msg_1","role":"assistant","model":"claude-opus-4-8","usage":{"input_tokens":1000000,"output_tokens":100000,"cache_creation_input_tokens":0,"cache_read_input_tokens":1000000}}}'
    recs = [
        m._claude_records_from_line(base % u, ".claude")
        for u in ("blkA", "blkB", "blkC")
    ]
    check(
        all((r["id"] == "msg_1" for r in recs)),
        "dedup id = API message.id, NOT the line uuid",
    )
    nofb = m._claude_records_from_line(
        '{"uuid":"u9","message":{"role":"assistant","model":"claude-fable-5","usage":{"input_tokens":1,"output_tokens":1}}}',
        ".claude",
    )
    check(nofb["id"] == "u9", "no message.id/requestId -> falls back to uuid")
    saved = m.USAGE_LEDGER_DIR
    try:
        with tempfile.TemporaryDirectory() as st:
            m.USAGE_LEDGER_DIR = st
            m._ledger_append(recs)
            dd = m._read_usage_ledger(0)
            check(
                len(dd) == 1, "3 content-block lines of one turn -> ONE billed record"
            )
            u = m.gather_usage(0)
            check(
                u["grand"]["completions"] == 1
                and abs(u["grand"]["cost"] - 8.0) < 1e-06,
                "billed once = $8.00, not 3x ($24.00)",
            )
        with tempfile.TemporaryDirectory() as st2:
            m.USAGE_LEDGER_DIR = st2
            common = {
                "provider": "claude",
                "account": ".claude",
                "model": "claude-opus-4-8",
                "id": "msg_2",
                "kind": "add",
                "session": "s",
                "in": 0,
                "cw": 0,
                "cr": 0,
            }
            ts = int(m.iso_to_epoch("2026-06-01T00:00:00Z"))
            partial = dict(common, ts=ts, out=3)
            final = dict(common, ts=ts, out=1000000)
            m._ledger_append([partial, final])
            dd2 = m._read_usage_ledger(0)
            check(
                len(dd2) == 1 and dd2[0]["out"] == 1000000,
                "partial+final same id -> keep MAX output (1M), not the partial (3)",
            )
            u2 = m.gather_usage(0)
            check(
                abs(u2["grand"]["cost"] - 25.0) < 1e-06,
                "billed at final output: 1M opus output = $25, not partial",
            )
    finally:
        m.USAGE_LEDGER_DIR = saved


def test_usage_dates_and_pricing():
    """Real per-completion timestamps drive date-partitioning + by-date/by-session
    aggregation, and cache rates are the DOCUMENTED multipliers of input (not guessed)."""
    print("test_usage_dates_and_pricing")
    import time as _t

    for mdl in ("claude-opus-4-8", "claude-fable-5", "claude-haiku-4-5"):
        p = m.model_pricing(mdl)
        check(
            abs(p["cr"] - p["in"] * 0.1) < 1e-09,
            mdl + " cache-read = 0.10x input (documented)",
        )
        check(
            abs(p["cw"] - p["in"] * 1.25) < 1e-09,
            mdl + " cache-write = 1.25x input (documented)",
        )
    gp = m.model_pricing("gpt-5.5")
    check(abs(gp["cr"] - gp["in"] * 0.1) < 1e-09, "gpt-5.5 cached-input = 0.10x input")
    check(gp["cw"] == 0.0, "OpenAI bills no separate cache write")
    cl = '{"timestamp":"2026-05-30T12:00:00.000Z","sessionId":"sessZ","uuid":"ux","message":{"role":"assistant","model":"claude-fable-5","usage":{"input_tokens":10,"output_tokens":2,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}'
    r = m._claude_records_from_line(cl, ".claude")
    check(
        r["ts"] == int(m.iso_to_epoch("2026-05-30T12:00:00.000Z")),
        "claude record carries the line's real ts",
    )
    check(r["session"] == "sessZ", "claude record carries sessionId")
    cx = '{"timestamp":"2026-04-12T08:00:00.000Z","type":"token_count","info":{"total_token_usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":5}}}'
    r2 = m._codex_record_from_line(cx, ".codex", "rollA")
    check(
        r2["ts"] == int(m.iso_to_epoch("2026-04-12T08:00:00.000Z"))
        and r2["session"] == "rollA",
        "codex record carries real ts + session",
    )
    saved = m.USAGE_LEDGER_DIR
    try:
        with tempfile.TemporaryDirectory() as st:
            m.USAGE_LEDGER_DIR = st
            t_may = int(m.iso_to_epoch("2026-05-30T12:00:00Z"))
            t_jun = int(m.iso_to_epoch("2026-06-10T12:00:00Z"))
            m._ledger_append(
                [
                    {
                        "ts": t_may,
                        "provider": "claude",
                        "account": ".claude",
                        "model": "claude-fable-5",
                        "id": "a",
                        "kind": "add",
                        "session": "s1",
                        "in": 1000000,
                        "out": 0,
                        "cw": 0,
                        "cr": 0,
                    },
                    {
                        "ts": t_jun,
                        "provider": "claude",
                        "account": ".claude",
                        "model": "claude-fable-5",
                        "id": "b",
                        "kind": "add",
                        "session": "s2",
                        "in": 2000000,
                        "out": 0,
                        "cw": 0,
                        "cr": 0,
                    },
                ]
            )
            files = sorted(os.listdir(st))
            check(
                files == ["2026-05-30.jsonl", "2026-06-10.jsonl"],
                "ledger writes each record into its REAL date's file",
            )
            u = m.gather_usage(0)
            dates = [d["date"] for d in u["by_date"]]
            check(
                dates == ["2026-06-10", "2026-05-30"],
                "by_date present, ordered newest-first",
            )
            jun = next((d for d in u["by_date"] if d["date"] == "2026-06-10"))
            check(
                jun["in"] == 2000000 and abs(jun["cost"] - 20.0) < 1e-06,
                "by_date totals are per-day",
            )
            sids = {s["session"] for s in u["by_session"]}
            check(sids == {"s1", "s2"}, "by_session groups completions by session id")
    finally:
        m.USAGE_LEDGER_DIR = saved


def test_usage_429_backoff():
    """A 429 from the usage endpoint (per-token rate limit — live statuslines poll it too)
    must back off: serve the recent cache unlabeled, record rl_until, and make NO further
    HTTP attempts until the backoff expires."""
    print("test_usage_429_backoff")
    import time as _t

    saved = (
        m.USAGE_DIR,
        m.http_get_json,
        m.claude_oauth_token,
        m._claude_local_identity,
    )
    try:
        with tempfile.TemporaryDirectory() as st:
            m.USAGE_DIR = st
            os.makedirs(st, exist_ok=True)
            cache = os.path.join(st, "claude-.claude-acct9.json")
            json.dump(
                {
                    "five_hour": {"used_percent": 55, "resets_at": _t.time() + 3600},
                    "seven_day": {"used_percent": 60, "resets_at": None},
                    "source": "claude-api",
                    "observed_at": int(_t.time()) - 90,
                },
                open(cache, "w"),
            )
            m.claude_oauth_token = lambda p: "tok"
            m._claude_local_identity = lambda p: {}
            calls = []

            class RL(Exception):
                code = 429

            def boom429(*a, **k):
                calls.append(1)
                raise RL("rate limited")

            m.http_get_json = boom429
            fresh = m.USAGE_FRESH_S
            try:
                m.USAGE_FRESH_S = 0
                w = m.fetch_claude_usage("/h/.claude-acct9")
                check(
                    w and w["five_hour"]["used_percent"] == 55 and (not w.get("stale")),
                    "429 -> serve recent cache, NO stale label, no hammering",
                )
                check(
                    len(calls) == 1, "429 is NOT retried (retry would extend the limit)"
                )
                d = json.load(open(cache))
                check(
                    d.get("rl_until", 0) > _t.time(),
                    "backoff recorded in the cache (rl_until)",
                )
                w2 = m.fetch_claude_usage("/h/.claude-acct9")
                check(
                    len(calls) == 1 and w2 and (w2["five_hour"]["used_percent"] == 55),
                    "during backoff: served from cache with ZERO HTTP attempts",
                )
            finally:
                m.USAGE_FRESH_S = fresh
    finally:
        (
            m.USAGE_DIR,
            m.http_get_json,
            m.claude_oauth_token,
            m._claude_local_identity,
        ) = saved


def test_usage_watch_self_heal_backfill():
    """On a machine where the state says baselined but the ledger is empty while transcripts
    exist (fresh computer / lost ledger), the watcher self-heals: resets state and re-runs a
    FULL backfill — so the dashboard always shows that machine's full local history."""
    print("test_usage_watch_self_heal_backfill")
    saved = (
        m.USAGE_WATCH_STATE,
        m.USAGE_LEDGER_DIR,
        m._usage_session_files,
        m.usage_watch_pass,
        m.keepalive_pass,
        m._save_watch_state,
    )
    try:
        with tempfile.TemporaryDirectory() as st:
            m.USAGE_WATCH_STATE = os.path.join(st, "watch.json")
            m.USAGE_LEDGER_DIR = os.path.join(st, "ledger")
            with open(m.USAGE_WATCH_STATE, "w") as f:
                json.dump({"baselined": True, "files": {"/old/path.jsonl": 999}}, f)
            m._usage_session_files = lambda since_ts=0: [
                ("/t/x.jsonl", ".claude", "claude", None)
            ]
            calls = []

            def fake_pass(state, baseline=False, full=False):
                calls.append(
                    {
                        "baseline": baseline,
                        "full": full,
                        "had_old_offsets": "files" in state,
                    }
                )
                return (state, 0)

            m.usage_watch_pass = fake_pass
            m.keepalive_pass = lambda s: 0
            m._save_watch_state = lambda s: None
            m.cmd_usage_watch(["--once"])
            check(
                len(calls) >= 1 and calls[0]["full"] and (not calls[0]["baseline"]),
                "empty ledger + transcripts present -> FULL re-backfill despite baselined state",
            )
            check(
                not calls[0]["had_old_offsets"],
                "state reset -> stale offsets dropped before rescan",
            )
            os.makedirs(m.USAGE_LEDGER_DIR, exist_ok=True)
            with open(os.path.join(m.USAGE_LEDGER_DIR, "2026-06-11.jsonl"), "w") as f:
                f.write("{}\n")
            with open(m.USAGE_WATCH_STATE, "w") as f:
                json.dump({"baselined": True}, f)
            calls.clear()
            m.cmd_usage_watch(["--once"])
            check(
                len(calls) == 1 and (not calls[0]["full"]),
                "ledger present -> normal incremental sweep only (no re-backfill)",
            )
    finally:
        (
            m.USAGE_WATCH_STATE,
            m.USAGE_LEDGER_DIR,
            m._usage_session_files,
            m.usage_watch_pass,
            m.keepalive_pass,
            m._save_watch_state,
        ) = saved


def test_usage_since_window():
    """`neomax usage --since <Nm|Nh>` sums the WHOLE-FLEET ledger over a precise recent
    window (so a finished goal/session's full cost — orchestrator + workers + sub-agents —
    can be reported), and _parse_duration_s handles the suffixes."""
    print("test_usage_since_window")
    import io, contextlib, time as _t

    check(
        m._parse_duration_s("37m") == 2220
        and m._parse_duration_s("2h") == 7200
        and (m._parse_duration_s("90s") == 90)
        and (m._parse_duration_s("1d") == 86400)
        and (m._parse_duration_s("500") == 500)
        and (m._parse_duration_s("2.5h") == 9000),
        "_parse_duration_s: s/m/h/d + bare-number(seconds)",
    )
    check(
        m._parse_duration_s("junk") is None and m._parse_duration_s("") is None,
        "_parse_duration_s: garbage -> None",
    )
    sd = os.environ.get("NEOMAX_HOME")
    with tempfile.TemporaryDirectory() as st:
        os.environ["NEOMAX_HOME"] = st
        sv = m.USAGE_LEDGER_DIR
        m.USAGE_LEDGER_DIR = os.path.join(st, "usage-ledger")
        os.makedirs(m.USAGE_LEDGER_DIR)
        try:
            now = int(_t.time())
            day = _t.strftime("%Y-%m-%d", _t.localtime(now))
            recent = now - 600
            old = now - 4000
            with open(os.path.join(m.USAGE_LEDGER_DIR, day + ".jsonl"), "w") as f:
                f.write(
                    json.dumps(
                        {
                            "id": "r1",
                            "ts": recent,
                            "provider": "codex",
                            "account": ".codex",
                            "model": "gpt-5.5",
                            "kind": "total",
                            "in": 1000,
                            "out": 500,
                            "cw": 0,
                            "cr": 100,
                        }
                    )
                    + "\n"
                )
                f.write(
                    json.dumps(
                        {
                            "id": "r2",
                            "ts": old,
                            "provider": "codex",
                            "account": ".codex",
                            "model": "gpt-5.5",
                            "kind": "total",
                            "in": 9999,
                            "out": 9999,
                            "cw": 0,
                            "cr": 0,
                        }
                    )
                    + "\n"
                )
            u = m.gather_usage(1800 / 86400.0)
            check(
                u["grand"]["completions"] == 1 and u["grand"]["in"] == 1000,
                "--since 30m sums only the in-window fleet record (older excluded)",
            )
            u2 = m.gather_usage(5400 / 86400.0)
            check(
                u2["grand"]["completions"] == 2,
                "wider window includes the older record too",
            )
            buf = io.StringIO()
            import sys as _sys

            old_argv = None
            with contextlib.redirect_stdout(buf):
                m.cmd_usage(["--since", "30m"])
            out = buf.getvalue()
            check(
                "WHOLE FLEET" in out and "last 30m" in out,
                "usage --since labels the window + WHOLE-FLEET scope",
            )
            jb = io.StringIO()
            with contextlib.redirect_stdout(jb):
                m.cmd_usage(["--json", "--since", "30m"])
            j = json.loads(jb.getvalue())
            check(
                j.get("window") == "last 30m" and j["grand"]["completions"] == 1,
                "usage --json --since carries the window label + windowed totals",
            )
        finally:
            m.USAGE_LEDGER_DIR = sv
            if sd is None:
                os.environ.pop("NEOMAX_HOME", None)
            else:
                os.environ["NEOMAX_HOME"] = sd


def test_usage_watch_malformed_token_values():
    """A transcript completion with a NON-numeric usage value must be skipped per-line,
    never crash usage_watch_pass — under launchd KeepAlive a crash respawns in 10s and
    re-reads the same poisoned line forever (the respawn-storm fix)."""
    print("test_usage_watch_malformed_token_values")
    saved = (m.USAGE_LEDGER_DIR, m._solo_profile)
    saved_env = {
        k: os.environ.get(k) for k in ("NEOMAX_PROFILES", "NEOMAX_CODEX_PROFILES")
    }
    try:
        with tempfile.TemporaryDirectory() as st:
            m._solo_profile = lambda: os.path.join(st, ".claude-solo-none")
            cprof = os.path.join(st, ".claude")
            proj = os.path.join(cprof, "projects", "-p")
            os.makedirs(proj)
            xprof = os.path.join(st, ".codex")
            sess = os.path.join(xprof, "sessions", "2026")
            os.makedirs(sess)
            m.USAGE_LEDGER_DIR = os.path.join(st, "ledger")
            os.environ["NEOMAX_PROFILES"] = cprof
            os.environ["NEOMAX_CODEX_PROFILES"] = xprof

            def aline(mid, usage):
                return json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "s1",
                        "message": {
                            "role": "assistant",
                            "id": mid,
                            "model": "claude-fable-5",
                            "usage": usage,
                        },
                    }
                )

            with open(os.path.join(proj, "s1.jsonl"), "w") as f:
                f.write(aline("msg_1", {"input_tokens": 5, "output_tokens": 7}) + "\n")
                f.write(
                    aline("msg_2", {"input_tokens": "abc", "output_tokens": 7}) + "\n"
                )
                f.write(
                    aline("msg_3", {"input_tokens": [1, 2], "output_tokens": 7}) + "\n"
                )
                f.write(aline("msg_4", {"input_tokens": 1, "output_tokens": 2}) + "\n")

            def cline(tot):
                return json.dumps(
                    {
                        "payload": {
                            "type": "token_count",
                            "info": {"total_token_usage": tot},
                        }
                    }
                )

            with open(os.path.join(sess, "r1.jsonl"), "w") as f:
                f.write(
                    cline(
                        {
                            "input_tokens": 10,
                            "cached_input_tokens": 0,
                            "output_tokens": 3,
                        }
                    )
                    + "\n"
                )
                f.write(
                    cline(
                        {
                            "input_tokens": "garbage",
                            "cached_input_tokens": 0,
                            "output_tokens": 9,
                        }
                    )
                    + "\n"
                )
                f.write(
                    cline(
                        {
                            "input_tokens": 40,
                            "cached_input_tokens": 0,
                            "output_tokens": 9,
                        }
                    )
                    + "\n"
                )
            (state, total) = m.usage_watch_pass({}, full=True)
            check(
                total == 4,
                "poisoned lines skipped; 2 claude + 2 codex good records captured",
            )
            ids = set()
            for name in os.listdir(m.USAGE_LEDGER_DIR):
                with open(os.path.join(m.USAGE_LEDGER_DIR, name)) as f:
                    ids.update((json.loads(l)["id"] for l in f if l.strip()))
            check(
                "msg_1" in ids and "msg_4" in ids,
                "good lines AFTER a poisoned one still captured",
            )
            check(
                "msg_2" not in ids and "msg_3" not in ids,
                "malformed records skipped, not mangled",
            )
            (_, total2) = m.usage_watch_pass(state)
            check(
                total2 == 0, "offsets advanced past poisoned lines (no re-read storm)"
            )
    finally:
        (m.USAGE_LEDGER_DIR, m._solo_profile) = saved
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
