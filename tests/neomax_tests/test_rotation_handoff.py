"""In-place rotation, provider-neutral handoff, armed markers, and anti-herd behavior."""

from .support import *


def test_session_rotate():
    """/rotate: any session rotates its OWN profile in place to the freshest other account at
    >=threshold (no /login) by SWAPPING places — the maxed account moves INTO the fresh account's
    profile slot (cooled by uuid AND profile until its 5h reset) so the fleet never ends up with
    two profiles on the same account; no fresher account -> waits, never strands."""
    print("test_session_rotate")
    keys = (
        "_current_session_profile",
        "engine_profiles",
        "logged_in",
        "is_paused",
        "usage_window",
        "weekly_window",
        "_read_oauth_account",
        "fetch_claude_usage",
        "_swap_claude_auth",
        "_log_auth_rotation",
        "read_claude_cred_blob",
        "_dump_private",
        "_profile_recently_active",
        "set_cooldown",
        "clear_cooldown",
        "live_counts",
    )
    saved = {k: getattr(m, k) for k in keys}
    m.live_counts = lambda e: {}
    with tempfile.TemporaryDirectory() as st:
        (sv_state, sv_acct) = (m.STATE_DIR, m.ACCOUNT_COOLDOWN_FILE)
        sv_bk = m.AUTH_BACKUPS
        m.STATE_DIR = st
        m.ACCOUNT_COOLDOWN_FILE = os.path.join(st, "account-cooldown.json")
        m.AUTH_BACKUPS = os.path.join(st, "backups")
        try:
            (P0, P1, P2) = ("/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3")
            oa = {
                P0: {"accountUuid": "A", "emailAddress": "a@x"},
                P1: {"accountUuid": "B", "emailAddress": "b@x"},
                P2: {"accountUuid": "C", "emailAddress": "c@x"},
            }
            five = {P0: 50.0, P1: 10.0, P2: 5.0}
            swapped = []
            prof_cooled = {}
            m._current_session_profile = lambda: P0
            m.engine_profiles = lambda e: [P0, P1, P2]
            m.logged_in = lambda p, e: True
            m.is_paused = lambda p: False
            m.usage_window = lambda p, e: five[p]
            m.weekly_window = lambda p, e: 0.0
            m._read_oauth_account = lambda p: oa.get(os.path.abspath(p), oa.get(p, {}))
            m.fetch_claude_usage = lambda p: {
                "five_hour": {"used_percent": five.get(p, 0), "resets_at": 4102444800}
            }
            m._log_auth_rotation = lambda d: None
            m.read_claude_cred_blob = lambda p: "blob"
            m._dump_private = lambda path, obj: None
            m._profile_recently_active = lambda p, within_s=900: False
            m.set_cooldown = lambda p, until: prof_cooled.__setitem__(p, until)
            m.clear_cooldown = lambda p: prof_cooled.pop(p, None)

            def fake_swap(a, b):
                swapped.append((a, b))
                (oa[a], oa[b]) = (dict(oa[b]), dict(oa[a]))
                (five[a], five[b]) = (five[b], five[a])
                return True

            m._swap_claude_auth = fake_swap
            check(
                "no rotation" in m.session_rotate(threshold=97.0) and (not swapped),
                "below threshold -> no-op",
            )
            five[P0] = 98.0
            msg = m.session_rotate(threshold=97.0)
            check(
                "ROTATED" in msg
                and "swapped" in msg.lower()
                and (swapped == [(P0, P2)]),
                "at threshold -> SWAP current profile with freshest other (C)",
            )
            check(
                oa[P0]["accountUuid"] == "C" and oa[P2]["accountUuid"] == "A",
                "after swap P0 runs C (fresh) and the maxed A now lives in P2's slot (no duplicate)",
            )
            check(
                m._account_cooled("A"),
                "the maxed account A is cooled by accountUuid until its 5h reset",
            )
            check(
                prof_cooled.get(P2) and P0 not in prof_cooled,
                "P2 (now holding maxed A) is profile-cooled; P0 (now fresh) is cleared",
            )
            check(not m._account_cooled("B"), "an untouched account is not cooled")
            swapped.clear()
            five[P0] = 99.0
            m._cool_account("B", 4102444800)
            msg = m.session_rotate(threshold=97.0)
            check(
                "NO other" in msg and (not swapped),
                "all other accounts cooled -> wait for a 5h reset",
            )
            try:
                os.remove(m.ACCOUNT_COOLDOWN_FILE)
            except OSError:
                pass
            prof_cooled.clear()
            swapped.clear()
            oa[P0] = {"accountUuid": "A", "emailAddress": "a@x"}
            oa[P1] = {"accountUuid": "B", "emailAddress": "b@x"}
            oa[P2] = {"accountUuid": "C", "emailAddress": "c@x"}
            (five[P0], five[P1], five[P2]) = (30.0, 10.0, 80.0)
            msg = m.session_rotate(threshold=97.0, prefer=["3"], force=True)
            check(
                "ROTATED" in msg and swapped == [(P0, P2)],
                "explicit target -> rotates to the NAMED account, not the freshest, even under threshold",
            )
            check(
                oa[P0]["accountUuid"] == "C" and oa[P2]["accountUuid"] == "A",
                "explicit swap lands the session on the requested account (C); current (A) moves to its slot",
            )
            check(
                not m._account_cooled("A"),
                "a forced move of a still-fresh account does NOT cool it (only spent accounts cool)",
            )
            swapped.clear()
            msg = m.session_rotate(threshold=97.0, prefer=["1"], force=True)
            check(
                "can't be rotated" in msg and (not swapped),
                "explicit request for the current/unusable account refuses cleanly",
            )
            check(
                m._parse_account_selectors(["account", "2"]) == ["2"]
                and m._parse_account_selectors(["one", "acct3", "to", "orch"])
                == ["1", "3", "orch"],
                "/rotate selector parser normalizes loose tokens",
            )
            for f in (m.ACCOUNT_COOLDOWN_FILE,):
                try:
                    os.remove(f)
                except OSError:
                    pass
            prof_cooled.clear()
            swapped.clear()
            oa[P0] = {"accountUuid": "A", "emailAddress": "a@x"}
            oa[P1] = {"accountUuid": "B", "emailAddress": "b@x"}
            oa[P2] = {"accountUuid": "C", "emailAddress": "c@x"}
            (five[P0], five[P1], five[P2]) = (6.0, 15.0, 0.0)
            msg = m.session_rotate(threshold=97.0, force=True)
            check(
                "ROTATED" in msg and swapped == [(P0, P2)],
                "manual /rotate under threshold MOVES to the most-available account (not a no-op)",
            )
            check(
                not m._account_cooled("A"),
                "the still-fresh account moved off is NOT cooled (manual move)",
            )
            try:
                os.remove(m.ACCOUNT_COOLDOWN_FILE)
            except OSError:
                pass
            swapped.clear()
            oa[P0] = {"accountUuid": "A", "emailAddress": "a@x"}
            oa[P1] = {"accountUuid": "B", "emailAddress": "b@x"}
            oa[P2] = {"accountUuid": "C", "emailAddress": "c@x"}
            (five[P0], five[P1], five[P2]) = (2.0, 15.0, 9.0)
            msg = m.session_rotate(threshold=97.0, force=True)
            check(
                "already on the most available" in msg and (not swapped),
                "manual /rotate when already freshest -> stays put (no churn)",
            )
            swapped.clear()
            five[P0] = 30.0
            check(
                "no rotation yet" in m.session_rotate(threshold=97.0) and (not swapped),
                "armed monitor (force=False) does NOT swap under threshold",
            )
        finally:
            (m.STATE_DIR, m.ACCOUNT_COOLDOWN_FILE, m.AUTH_BACKUPS) = (
                sv_state,
                sv_acct,
                sv_bk,
            )
            for k, v in saved.items():
                setattr(m, k, v)


def test_orchestrator_handoff():
    print("test_orchestrator_handoff")
    saved = {
        k: os.environ.get(k)
        for k in (
            "CLAUDE_CONFIG_DIR",
            "CODEX_HOME",
            "XDG_DATA_HOME",
            "KIMI_CODE_HOME",
            "GROK_HOME",
            "NEOMAX_ROLE",
        )
    }
    try:
        for k in saved:
            os.environ.pop(k, None)
        check(
            m.orchestrator_engine() == "claude", "default orchestrator engine = claude"
        )
        check(
            m.current_orchestrator_profile() == m.ENGINES["claude"]["default_dir"],
            "unset config env -> default ~/.claude (account 1)",
        )
        os.environ["NEOMAX_ROLE"] = "codex"
        check(
            m.orchestrator_engine() == "codex",
            "NEOMAX_ROLE=codex -> codex orchestrator",
        )
        os.environ["CODEX_HOME"] = "/x/.codex-acct2"
        check(
            m.current_orchestrator_profile() == "/x/.codex-acct2",
            "codex orchestrator profile from CODEX_HOME",
        )
        os.environ["NEOMAX_ROLE"] = "claude"
        os.environ["CLAUDE_CONFIG_DIR"] = "/x/.claude-acct2"
        check(
            m.current_orchestrator_profile() == "/x/.claude-acct2",
            "claude orchestrator profile from CLAUDE_CONFIG_DIR",
        )
        os.environ["NEOMAX_ROLE"] = "opencode"
        os.environ["XDG_DATA_HOME"] = "/x/.opencode-acct2"
        check(
            m.orchestrator_engine() == "opencode"
            and m.current_orchestrator_profile() == "/x/.opencode-acct2",
            "OpenCode orchestrator profile from XDG_DATA_HOME",
        )
        os.environ["NEOMAX_ROLE"] = "kimi"
        os.environ["KIMI_CODE_HOME"] = "/x/.kimi-code-acct2"
        check(
            m.orchestrator_engine() == "kimi"
            and m.current_orchestrator_profile() == "/x/.kimi-code-acct2",
            "Kimi orchestrator profile from KIMI_CODE_HOME",
        )
        os.environ["NEOMAX_ROLE"] = "grok"
        os.environ["GROK_HOME"] = "/x/.grok-acct2"
        check(
            m.orchestrator_engine() == "grok"
            and m.current_orchestrator_profile() == "/x/.grok-acct2",
            "Grok orchestrator profile from GROK_HOME",
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    sv_state_h = m.STATE_DIR
    _h_td = tempfile.mkdtemp()
    m.STATE_DIR = _h_td
    sv_lc = m.live_counts
    sv_paused = m.is_paused
    m.live_counts = lambda e: {}
    try:
        profs = ["/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3"]
        paused = set()
        m.engine_profiles = lambda e: profs
        m.logged_in = lambda p, e="claude": True
        m.is_paused = lambda p: p in paused
        m.cooling_down = lambda p: 0
        five = {profs[0]: 96.0, profs[1]: 40.0, profs[2]: 10.0}
        wk = {p: 30.0 for p in profs}
        m.usage_window = lambda p, e="claude": five[p]
        m.weekly_window = lambda p, e="claude": wk[p]
        check(
            m.pick_freshest_account("claude", exclude={profs[0]}) == profs[2],
            "picks the lowest-5h account with headroom",
        )
        paused.add(profs[2])
        check(
            m.pick_freshest_account("claude", exclude={profs[0]}) == profs[1],
            "skips a manually paused handoff target",
        )
        paused.clear()
        wk[profs[2]] = 99.0
        check(
            m.pick_freshest_account("claude", exclude={profs[0]}) == profs[1],
            "skips a near-weekly-maxed landing spot",
        )
        five = {profs[0]: 96.0, profs[1]: 95.0, profs[2]: 93.0}
        m.usage_window = lambda p, e="claude": five[p]
        wk = {p: 20.0 for p in profs}
        m.weekly_window = lambda p, e="claude": wk[p]
        check(
            m.pick_freshest_account("claude", exclude={profs[0]}) is None,
            "no account with 5h headroom -> None",
        )
    finally:
        m.STATE_DIR = sv_state_h
        m.live_counts = sv_lc
        m.is_paused = sv_paused
        import shutil as _sh

        _sh.rmtree(_h_td, ignore_errors=True)
    check(
        m.ORCH_5H_ROTATE == 99.0 and m.ORCH_7D_ROTATE == 99.0,
        "rotate thresholds are 99%% (5h) / 99%% (weekly)",
    )
    sc = "cd %s && cmax 2 %s" % (m._shell_quote("/p"), m._shell_quote('say "hi"'))
    appl = m._osa_escape(sc)
    check(
        '\\"hi\\"' in appl and sc.count("'") >= 4,
        "handoff osascript/shell quoting is escaped",
    )


def test_handoff_orch_account_formatting():
    """cmd_handoff must format the reserved 'orch' account (a STRING acct_no) without
    a TypeError — the %d -> %s fix in the check/kickoff/shell_cmd/dry-run strings."""
    print("test_handoff_orch_account_formatting")
    import io, contextlib

    keys = (
        "NEOMAX_ROLE",
        "NEOMAX_FLEET",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "NEOMAX_CLAUDE_ORCH",
        "SSH_CONNECTION",
        "SSH_TTY",
        "NEOMAX_CLAUDE_MODEL",
        "NEOMAX_CODEX_MODEL",
        "NEOMAX_OPENCODE_MODEL",
        "NEOMAX_KIMI_MODEL",
        "NEOMAX_GROK_MODEL",
    )
    saved_env = {k: os.environ.get(k) for k in keys}
    saved = (
        m.usage_window,
        m.weekly_window,
        m.pick_freshest_account,
        m.account_identity,
        m.STATE_DIR,
        m.EVENTS_DIR,
    )
    try:
        with tempfile.TemporaryDirectory() as st:
            for k in keys:
                os.environ.pop(k, None)
            orch = os.path.join(st, "claude-orch")
            os.makedirs(orch)
            m.STATE_DIR = st
            m.EVENTS_DIR = os.path.join(st, "events")
            os.environ["NEOMAX_ROLE"] = "claude"
            os.environ["NEOMAX_CLAUDE_ORCH"] = orch
            m.usage_window = lambda p, e=None: 99.5
            m.weekly_window = lambda p, e=None: 50.0
            m.account_identity = lambda p, e=None: {}
            m.pick_freshest_account = lambda engine, exclude=(): "/x/.claude-acct2"
            os.environ["CLAUDE_CONFIG_DIR"] = orch
            (out, code) = (io.StringIO(), None)
            try:
                with contextlib.redirect_stdout(out):
                    m.cmd_handoff(["--check"])
            except SystemExit as e:
                code = e.code
            check(
                code == 10 and "account orch" in out.getvalue(),
                "handoff --check formats acct_no='orch' (%s not %d) + exits 10 when advised",
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                m.cmd_handoff(["--dry-run"])
            check(
                "account orch" in out.getvalue() and "account 2" in out.getvalue(),
                "dry-run plan prints orch -> account 2 without TypeError",
            )
            baton = json.load(open(os.path.join(st, "handoff.json")))
            check(
                baton["from_account"] == "orch" and baton["to_account"] == 2,
                "handoff baton records from_account='orch'",
            )
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            os.environ["SSH_CONNECTION"] = "t"
            model_values = {
                "claude": "claude-sonnet-custom",
                "codex": "codex-custom",
                "opencode": "provider/custom",
                "kimi": "kimi-custom",
                "grok": "grok-custom",
            }
            for engine, model in model_values.items():
                os.environ["NEOMAX_%s_MODEL" % engine.upper()] = model
            os.environ["NEOMAX_FLEET"] = "claude,kimi"
            m.pick_freshest_account = lambda engine, exclude=(): orch
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                m.cmd_handoff([])
            handoff_out = out.getvalue()
            check(
                "account orch" in handoff_out
                and "cmax '--orchestrator'" in handoff_out,
                "target_no='orch' uses the launcher's dedicated-orchestrator flag",
            )
            check(
                "'--workers' 'claude,kimi'" in handoff_out,
                "handoff preserves the current worker scope",
            )
            check(
                all(
                    (
                        "--%s-model" % engine in handoff_out and model in handoff_out
                        for (engine, model) in model_values.items()
                    )
                ),
                "handoff preserves every configured orchestrator/worker model",
            )
    finally:
        (
            m.usage_window,
            m.weekly_window,
            m.pick_freshest_account,
            m.account_identity,
            m.STATE_DIR,
            m.EVENTS_DIR,
        ) = saved
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_universal_provider_rotate():
    """One `/rotate` surface follows the running provider; no provider-specific executable is
    needed. Claude keeps its in-place primitive, while the four isolated-profile providers launch
    a same-provider handoff with the current scope and every model selection intact."""
    print("test_universal_provider_rotate")
    import contextlib, io

    env_keys = (
        "NEOMAX_ROLE",
        "NEOMAX_FLEET",
        "SSH_CONNECTION",
        "SSH_TTY",
        "NEOMAX_CLAUDE_MODEL",
        "NEOMAX_CODEX_MODEL",
        "NEOMAX_OPENCODE_MODEL",
        "NEOMAX_KIMI_MODEL",
        "NEOMAX_GROK_MODEL",
    )
    saved_env = {key: os.environ.get(key) for key in env_keys}
    names = (
        "cmd_session_rotate",
        "current_orchestrator_profile",
        "_resolve_profile",
        "profile_acct_no",
        "logged_in",
        "is_paused",
        "cooling_down",
        "usage_window",
        "weekly_window",
        "account_identity",
    )
    saved = {name: getattr(m, name) for name in names}
    (saved_state, saved_events) = (m.STATE_DIR, m.EVENTS_DIR)
    try:
        with tempfile.TemporaryDirectory() as st:
            m.STATE_DIR = st
            m.EVENTS_DIR = os.path.join(st, "events")
            current = {engine: os.path.join(st, "." + engine) for engine in m.ENGINES}
            target = {
                engine: os.path.join(st, "." + engine + "-acct2")
                for engine in m.ENGINES
            }
            m.current_orchestrator_profile = lambda engine=None: current[engine]
            m._resolve_profile = lambda selector, engine: (
                target[engine] if str(selector) == "2" else None
            )
            m.profile_acct_no = lambda profile, engine: (
                2 if profile == target[engine] else 1
            )
            m.logged_in = lambda profile, engine=None: True
            m.is_paused = lambda profile: False
            m.cooling_down = lambda profile: 0
            m.usage_window = lambda profile, engine=None: 10.0
            m.weekly_window = lambda profile, engine=None: 10.0
            m.account_identity = lambda profile, engine=None: {
                "email": engine + "-2@example.invalid"
            }
            os.environ["NEOMAX_FLEET"] = "claude,kimi"
            os.environ["SSH_CONNECTION"] = "fixture"
            os.environ.pop("SSH_TTY", None)
            model_values = {
                "claude": "claude-custom",
                "codex": "codex-custom",
                "opencode": "provider/custom",
                "kimi": "kimi-custom",
                "grok": "grok-custom",
            }
            for engine, model in model_values.items():
                os.environ["NEOMAX_%s_MODEL" % engine.upper()] = model
            claude_args = []
            m.cmd_session_rotate = lambda argv: claude_args.append(list(argv))
            m.cmd_rotate(["--engine", "claude", "--arm", "2"])
            check(
                claude_args == [["--arm", "2"]],
                "universal /rotate routes Claude to its in-place account swap",
            )
            launchers = {
                "codex": "cdxmax",
                "opencode": "ocmax",
                "kimi": "kmax",
                "grok": "gmax",
            }
            for engine, launcher in launchers.items():
                os.environ["NEOMAX_ROLE"] = engine
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    m.cmd_rotate(["--engine", engine, "2"])
                text = out.getvalue()
                check(
                    "ROTATED orchestrator" in text
                    and launcher in text
                    and ("manual /rotate" in text),
                    engine + " /rotate performs a same-provider handoff",
                )
                check(
                    "'--workers' 'claude,kimi'" in text,
                    engine + " /rotate preserves the current worker scope",
                )
                check(
                    all(
                        (
                            "'--%s-model' '%s'" % (name, value) in text
                            for (name, value) in model_values.items()
                        )
                    ),
                    engine + " /rotate preserves every configured model",
                )
    finally:
        (m.STATE_DIR, m.EVENTS_DIR) = (saved_state, saved_events)
        for name, value in saved.items():
            setattr(m, name, value)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_armed_rotate_marker():
    """Armed-rotation marker (the Stop-hook mechanism, NO background poller): `/rotate --arm` writes
    an unclaimed marker; the FIRST session's Stop hook CLAIMS it; only that session acts thereafter,
    so a leftover marker can never auto-rotate an UNRELATED later session; it ages out; clear removes."""
    print("test_armed_rotate_marker")
    import time as _t

    saved = (m.STATE_DIR, m.ARMED_ROTATE_FILE)
    with tempfile.TemporaryDirectory() as st:
        m.STATE_DIR = st
        m.ARMED_ROTATE_FILE = os.path.join(st, "armed-rotate.json")
        try:
            p = "/h/.claude-acct5"
            m.set_armed_rotate(p, 97.0, ["6"])
            a = m.armed_rotate_take(p, "S1")
            check(
                a and a["threshold"] == 97.0 and (a["prefer"] == ["6"]),
                "first session claims the armed marker",
            )
            check(
                m.armed_rotate_take(p, "S2") is None,
                "a DIFFERENT session (S2) on the same profile is NOT acted on — no rogue rotation",
            )
            check(
                m.armed_rotate_take(p, "S1") is not None,
                "the claiming session (S1) keeps acting",
            )
            check(
                m.armed_rotate_take("/h/.claude-other", "S1") is None,
                "an unarmed profile returns None",
            )
            d = m._armed_rotate_map()
            d[os.path.abspath(p)]["ts"] = int(_t.time()) - 13 * 3600
            m._write_armed_rotate(d)
            check(
                m.armed_rotate_take(p, "S1") is None,
                "an aged-out marker is ignored (dead/closed session)",
            )
            m.set_armed_rotate(p, 97.0, [])
            m.clear_armed_rotate(p)
            check(
                m.armed_rotate_take(p, "S1") is None,
                "clear_armed_rotate removes the marker",
            )
        finally:
            (m.STATE_DIR, m.ARMED_ROTATE_FILE) = saved


def test_rotation_antiherd_and_autorotate():
    """The 5-sessions-died-together fix: (1) concurrent rotations SPREAD instead of converging on
    one freshest account (rotation CLAIM + flock-serialized pick_least_loaded), and the claim never
    fools the 'already freshest?' self-gate; (2) session_rotate triggers on WEEKLY too, not just 5h;
    (3) an orchestrator session AUTO-ARMS hands-off rotation at 95%/99% via orient --hook (no AI),
    without clobbering a human /rotate --arm; (4) rotation_tick rotates an over-threshold session
    with NO model turn (the rescue for a session that's already rate-limited), skipping mid-turn ones."""
    print("test_rotation_antiherd_and_autorotate")
    import io, contextlib, shutil as _sh

    keys = (
        "usage_window",
        "weekly_window",
        "weekly_reset_at",
        "live_counts",
        "engine_profiles",
        "logged_in",
        "is_paused",
        "_read_oauth_account",
        "fetch_claude_usage",
        "_swap_claude_auth",
        "_log_auth_rotation",
        "read_claude_cred_blob",
        "_dump_private",
        "_profile_recently_active",
        "set_cooldown",
        "clear_cooldown",
        "_solo_profile",
        "orchestrator_engine",
        "_current_session_profile",
        "orient_directive",
    )
    saved = {k: getattr(m, k) for k in keys}
    (sv_state, sv_armed, sv_acct) = (
        m.STATE_DIR,
        m.ARMED_ROTATE_FILE,
        m.ACCOUNT_COOLDOWN_FILE,
    )
    sv_env = {
        k: os.environ.get(k) for k in ("NEOMAX_ROLE", "NEOMAX_MODE", "NEOMAX_WORKER")
    }
    st = tempfile.mkdtemp()
    m.STATE_DIR = st
    m.ARMED_ROTATE_FILE = os.path.join(st, "armed-rotate.json")
    m.ACCOUNT_COOLDOWN_FILE = os.path.join(st, "account-cooldown.json")
    try:
        (A, B, C) = ("/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3")
        five = {A: 50.0, B: 10.0, C: 11.0}
        oa = {
            A: {"accountUuid": "A", "emailAddress": "a@x"},
            B: {"accountUuid": "B", "emailAddress": "b@x"},
            C: {"accountUuid": "C", "emailAddress": "c@x"},
        }
        m.usage_window = lambda p, e=None: five[p]
        m.weekly_window = lambda p, e=None: 0.0
        m.weekly_reset_at = lambda p, e=None: None
        m.live_counts = lambda e: {}
        m.logged_in = lambda p, e=None: True
        m.is_paused = lambda p: False
        m.engine_profiles = lambda e: [A, B, C]
        m._read_oauth_account = lambda p: oa.get(os.path.abspath(p), oa.get(p, {}))
        m.fetch_claude_usage = lambda p: {
            "five_hour": {"used_percent": five[p], "resets_at": 4102444800}
        }
        m._log_auth_rotation = lambda d: None
        m.read_claude_cred_blob = lambda p: "blob"
        m._dump_private = lambda a, b: None
        m.set_cooldown = lambda p, u: None
        m.clear_cooldown = lambda p: None
        first = m.pick_least_loaded([B, C], "claude")
        second = m.pick_least_loaded([B, C], "claude")
        check(
            first == B and second == C,
            "anti-herd: a 2nd concurrent rotation avoids the 1st's claimed target (spreads B->C)",
        )
        check(
            m._rotation_claim_count(B) == 1,
            "the chosen target is CLAIMED (so other racing sessions see it taken)",
        )
        check(
            m.rotation_rank(B, "claude", include_claims=False)
            < m.rotation_rank(C, "claude", include_claims=False),
            "claim-free rank still prefers the freshest (B); only the live CLAIM spreads the 2nd pick",
        )
        swapped = []
        m._swap_claude_auth = lambda a, b: swapped.append((a, b)) or True
        m._profile_recently_active = lambda p, within_s=900: False
        m._current_session_profile = lambda: A
        wkmap = {A: 98.0, B: 0.0, C: 0.0}
        m.weekly_window = lambda p, e=None: wkmap[p]
        five.update({A: 40.0, B: 10.0, C: 11.0})
        msg = m.session_rotate(
            profile=A, threshold=97.0, weekly_threshold=98.0, force=False
        )
        check(
            "ROTATED" in msg and swapped,
            "session_rotate triggers on WEEKLY (98%) even with 5h headroom (40%)",
        )
        m.weekly_window = lambda p, e=None: 0.0
        os.environ["NEOMAX_ROLE"] = "claude"
        os.environ.pop("NEOMAX_MODE", None)
        os.environ.pop("NEOMAX_WORKER", None)
        m.orchestrator_engine = lambda: "claude"
        m.orient_directive = lambda: "ORIENT"
        with contextlib.redirect_stdout(io.StringIO()):
            m.cmd_orient(["--hook"])
        rec = m._armed_rotate_map().get(os.path.abspath(A))
        check(
            rec
            and rec.get("auto")
            and (rec["threshold"] == m.ORCH_5H_ROTATE)
            and (rec["weekly_threshold"] == m.ORCH_7D_ROTATE),
            "orient --hook AUTO-ARMS the orchestrator at 99%% 5h / 99%% weekly (hands-off, no AI)",
        )
        m.set_armed_rotate(A, 90.0, ["2"], weekly_threshold=95.0, auto=False)
        m.set_armed_rotate(
            A, m.ORCH_5H_ROTATE, None, weekly_threshold=m.ORCH_7D_ROTATE, auto=True
        )
        rec2 = m._armed_rotate_map().get(os.path.abspath(A))
        check(
            rec2["threshold"] == 90.0
            and (not rec2["auto"])
            and (rec2["prefer"] == ["2"]),
            "an auto-arm never overrides a human /rotate --arm directive",
        )
        m._solo_profile = lambda: os.path.join(st, ".claude-solo")
        m._armed_rotate_map
        d = m._armed_rotate_map()
        d.pop(os.path.abspath(A), None)
        m._write_armed_rotate(d)
        m.set_armed_rotate(
            A, m.ORCH_5H_ROTATE, None, weekly_threshold=m.ORCH_7D_ROTATE, auto=True
        )
        five.update({A: 99.5, B: 5.0, C: 6.0})
        swapped.clear()
        m._profile_recently_active = lambda p, within_s=900: True
        n0 = m.rotation_tick()
        check(
            n0 == 0 and (not swapped),
            "rotation_tick SKIPS a mid-turn (recently active) session (the Stop hook owns it)",
        )
        m._profile_recently_active = lambda p, within_s=900: False
        n1 = m.rotation_tick()
        check(
            n1 == 1 and swapped,
            "rotation_tick rotates an IDLE armed over-threshold session (model-free rescue)",
        )
    finally:
        (m.STATE_DIR, m.ARMED_ROTATE_FILE, m.ACCOUNT_COOLDOWN_FILE) = (
            sv_state,
            sv_armed,
            sv_acct,
        )
        _sh.rmtree(st, ignore_errors=True)
        for k, v in saved.items():
            setattr(m, k, v)
        for k, v in sv_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
