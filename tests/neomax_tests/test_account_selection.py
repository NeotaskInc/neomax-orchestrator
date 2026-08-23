"""Account eligibility, load spreading, pauses, solo mode, and orchestrator selection."""

from .support import *


def test_reset_normalization_and_cooldown():
    print("test_reset_normalization_and_cooldown")
    import time

    now = time.time()
    check(
        abs(m.to_epoch_seconds(now + 3600) - (now + 3600)) < 1,
        "epoch-seconds passthrough",
    )
    check(
        abs(m.to_epoch_seconds(int((now + 3600) * 1000)) - (now + 3600)) < 1,
        "epoch-MILLIS scaled to seconds",
    )
    check(m.to_epoch_seconds("nope") is None, "non-numeric -> None")
    check(
        m.to_epoch_seconds(0) is None and m.to_epoch_seconds(-5) is None,
        "non-positive -> None",
    )
    fut = now + 47 * 60
    check(
        abs(m.normalize_reset_epoch(fut) - fut) < 1,
        "near-future reset kept (rolling, <5h)",
    )
    check(m.normalize_reset_epoch(now - 100) is None, "already-past reset -> None")
    check(
        m.normalize_reset_epoch(now + 30 * 24 * 3600) is None,
        "absurd far-future -> None",
    )
    check(
        m.normalize_reset_epoch(int(fut * 1000)) is not None,
        "future epoch-millis normalized (not rejected)",
    )
    check(m.normalize_reset_epoch(None) is None, "missing -> None")
    with tempfile.TemporaryDirectory() as st:
        m.STATE_DIR = st
        m.COOLDOWN_FILE = os.path.join(st, "cooldown.json")
        prof = "/x/.codex-acct1"
        m.maybe_cooldown({"profile": prof, "resets_at": fut}, "limit")
        check(
            abs(m.cooling_down(prof) - fut) < 2,
            "cooldown uses ACTUAL reported reset (~47m, not 5h)",
        )
        m.maybe_cooldown({"profile": prof, "resets_at": None}, "limit")
        check(
            now + 25 * 60 < m.cooling_down(prof) < now + 35 * 60,
            "unknown reset -> ~30min fallback (not 5h)",
        )
        m.maybe_cooldown({"profile": prof, "resets_at": now - 100}, "limit")
        check(
            now + 25 * 60 < m.cooling_down(prof) < now + 35 * 60,
            "past reset -> 30min fallback",
        )
        m.set_cooldown(prof, fut * 1000)
        check(
            m.cooling_down(prof) <= now + m.MAX_RESET_HORIZON_S + 2,
            "set_cooldown clamps ms garbage (no 'forever')",
        )


def test_pick_account_spread_bias():
    print("test_pick_account_spread_bias")
    profs = ["/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3"]
    sv = {
        k: getattr(m, k)
        for k in (
            "engine_profiles",
            "logged_in",
            "cooling_down",
            "usage_window",
            "weekly_window",
            "live_counts",
        )
    }
    try:
        m.engine_profiles = lambda e: profs
        m.logged_in = lambda p, e: True
        m.cooling_down = lambda p: 0
        m.usage_window = lambda p, e: 0.0
        m.weekly_window = lambda p, e: 0.0
        m.live_counts = lambda e: {profs[0]: 12, profs[1]: 0, profs[2]: 0}
        check(
            m.pick_account("auto", engine="claude") in (profs[1], profs[2]),
            "no-bias selection avoids the busy (12-session) account",
        )
        check(
            m.pick_account("auto", engine="claude", bias={profs[1]: 100.0}) == profs[2],
            "bias spreads the next part to the other idle account",
        )
        check(
            m.pick_account(
                "auto", engine="claude", bias={profs[1]: 100.0, profs[2]: 100.0}
            )
            in (profs[1], profs[2]),
            "bias stacks on a worker acct, never disturbs the busy orchestrator acct",
        )
    finally:
        for k, v in sv.items():
            setattr(m, k, v)


def test_default_fanout_cap():
    """run-all's default concurrency scales to the fleet (lanes-per-account, clamped to the agent
    budget), REUSING accounts — not the old one-part-per-account cap."""
    print("test_default_fanout_cap")
    sv = {
        k: getattr(m, k)
        for k in (
            "engine_profiles",
            "logged_in",
            "AGENT_BUDGET_DEFAULT",
            "FANOUT_LANES_PER_ACCT",
        )
    }
    try:
        m.engine_profiles = lambda e: {
            "claude": ["c1", "c2", "c3", "c4", "c5", "c6"],
            "codex": ["x1", "x2", "x3"],
            "opencode": [],
            "kimi": [],
            "grok": [],
        }[e]
        m.FANOUT_LANES_PER_ACCT = 6
        m.AGENT_BUDGET_DEFAULT = 50
        m.logged_in = lambda p, e="claude": True
        check(
            m.default_fanout_cap() == 50,
            "9 accts × 6 lanes clamps to the 50 agent budget (reuses accounts, not 1-per-acct)",
        )
        m.logged_in = lambda p, e="claude": p in ("c1", "c2", "c3")
        check(
            m.default_fanout_cap() == 18,
            "3 logged-in accts × 6 lanes = 18 (budget 50 not hit)",
        )
        m.AGENT_BUDGET_DEFAULT = 10
        check(m.default_fanout_cap() == 10, "budget 10 clamps 3×6 down to 10")
        m.AGENT_BUDGET_DEFAULT = 50
        m.FANOUT_LANES_PER_ACCT = 1
        m.logged_in = lambda p, e="claude": True
        check(
            m.default_fanout_cap() == 9,
            "lanes=1 floors at the 9-account count, never below",
        )
        m.logged_in = lambda p, e="claude": False
        check(
            m.default_fanout_cap() == 1, "0 logged-in accounts → floor of 1 (never 0)"
        )
    finally:
        for k, v in sv.items():
            setattr(m, k, v)


def test_fleet_concurrency_cap():
    """The global fleet hard cap bounds total live workers: default_fanout_cap never exceeds it, and
    fleet_live_workers counts running registry workers across both engines."""
    print("test_fleet_concurrency_cap")
    sv = {
        k: getattr(m, k)
        for k in (
            "engine_profiles",
            "logged_in",
            "AGENT_BUDGET_DEFAULT",
            "FANOUT_LANES_PER_ACCT",
            "FLEET_CONCURRENCY_CAP",
            "all_runs",
            "worker_alive",
        )
    }
    try:
        m.engine_profiles = lambda e: {
            "claude": ["c1", "c2", "c3", "c4", "c5", "c6"],
            "codex": ["x1", "x2", "x3"],
            "opencode": [],
            "kimi": [],
            "grok": [],
        }[e]
        m.logged_in = lambda p, e="claude": True
        m.FANOUT_LANES_PER_ACCT = 6
        m.AGENT_BUDGET_DEFAULT = 200
        m.FLEET_CONCURRENCY_CAP = 50
        check(
            m.default_fanout_cap() == 50,
            "fleet cap (50) bounds the fan-out even when budget is 200",
        )
        m.FLEET_CONCURRENCY_CAP = 30
        check(
            m.default_fanout_cap() == 30,
            "lowering the fleet cap lowers the effective fan-out",
        )
        m.FLEET_CONCURRENCY_CAP = 50
        m.worker_alive = lambda rec: True
        m.all_runs = lambda: [
            {"engine": "claude", "status": "running"},
            {"engine": "codex", "status": "running"},
            {"engine": "claude", "status": "done"},
        ]
        check(
            m.fleet_live_workers() == 2,
            "fleet_live_workers counts running workers across engines",
        )
        m.worker_alive = lambda rec: False
        check(
            m.fleet_live_workers() == 0,
            "dead workers are not counted toward the fleet cap",
        )
        m.all_runs = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        check(
            m.fleet_live_workers() == 0,
            "a broken registry degrades to 0, never crashes the cap",
        )
    finally:
        for k, v in sv.items():
            setattr(m, k, v)


def test_even_usage_distribution():
    """Auto-selection keeps usage EVEN: it always picks the freshest (lowest 5h+7d) least-busy
    account — incl. the orchestrator's own when that account is the freshest — so no single
    account (or hard-excluded one) carries everything. An account accruing usage is naturally
    deprioritized; there is NO last-resort penalty on the orchestrator's account."""
    print("test_even_usage_distribution")
    saved_fns = {
        k: getattr(m, k)
        for k in (
            "engine_profiles",
            "worker_profiles",
            "logged_in",
            "usage_window",
            "weekly_window",
            "live_counts",
            "cooling_down",
        )
    }
    saved_env = {
        k: os.environ.get(k) for k in ("NEOMAX_ROLE", "CODEX_HOME", "NEOMAX_WORKER")
    }
    try:
        profs = ["/h/.codex", "/h/.codex-acct2", "/h/.codex-acct3"]
        for fn in ("engine_profiles", "worker_profiles"):
            setattr(m, fn, lambda e, _p=profs: _p)
        m.logged_in = lambda p, e: True
        m.weekly_window = lambda p, e: 0.0
        m.live_counts = lambda e: {p: 0 for p in profs}
        m.cooling_down = lambda p: 0
        os.environ["NEOMAX_ROLE"] = "codex"
        os.environ["CODEX_HOME"] = profs[0]
        os.environ.pop("NEOMAX_WORKER", None)
        usage = {profs[0]: 0.0, profs[1]: 0.0, profs[2]: 0.0}
        m.usage_window = lambda p, e: usage[p]
        picks = {m.pick_account("auto", engine="codex") for _ in range(6)}
        check(
            picks <= set(profs),
            "even/idle fleet -> any account incl. the orchestrator's own is eligible (no exclusion)",
        )
        usage = {profs[0]: 40.0, profs[1]: 5.0, profs[2]: 5.0}
        check(
            m.pick_account("auto", engine="codex") in (profs[1], profs[2]),
            "busier orchestrator account is naturally deprioritized -> evens out usage",
        )
        usage = {profs[0]: 2.0, profs[1]: 50.0, profs[2]: 50.0}
        check(
            m.pick_account("auto", engine="codex") == profs[0],
            "freshest account wins — including the orchestrator's own (it can start work)",
        )
        usage = {profs[0]: 18.0, profs[1]: 82.0, profs[2]: 73.0}
        live = {profs[0]: 5, profs[1]: 0, profs[2]: 3}
        m.live_counts = lambda e: dict(live)
        check(
            m.pick_account("auto", engine="codex") == profs[0],
            "fresh-5h orchestrator account (18%, 5 live) is picked over busy workers (82%/73%) — live count no longer starves it",
        )
        usage = {profs[0]: 21.0, profs[1]: 7.0, profs[2]: 6.0}
        live = {profs[0]: 0, profs[1]: 0, profs[2]: 0}
        m.live_counts = lambda e: dict(live)
        check(
            m.pick_account("auto", engine="codex") == profs[2],
            "1st task -> freshest 5h acct (6%)",
        )
        live[profs[2]] = 1
        check(
            m.pick_account("auto", engine="codex") == profs[1],
            "2nd task -> next-freshest 5h (7%)",
        )
        usage = {profs[0]: 0.0, profs[1]: 50.0, profs[2]: 50.0}
        live = {profs[0]: m.LIVE_CONCURRENCY_CAP, profs[1]: 0, profs[2]: 0}
        m.live_counts = lambda e: dict(live)
        check(
            m.pick_account("auto", engine="codex") in (profs[1], profs[2]),
            "an account at the concurrency cap is skipped (no over-stacking) even at 0% 5h",
        )
    finally:
        for k, v in saved_fns.items():
            setattr(m, k, v)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_pause_unpause():
    """A PAUSED account is hard-excluded from auto-dispatch (no tasks routed to it) until
    unpaused — live, read fresh from disk each pick (no orchestrator restart). State persists,
    survives an all-paused pool (refuses rather than dispatching to a paused acct), and an
    explicit `auto N` still overrides. gather_status surfaces the paused flag for the dashboard."""
    print("test_pause_unpause")
    saved_fns = {
        k: getattr(m, k)
        for k in (
            "engine_profiles",
            "worker_profiles",
            "logged_in",
            "usage_window",
            "weekly_window",
            "live_counts",
            "cooling_down",
        )
    }
    with tempfile.TemporaryDirectory() as st:
        (sv_state, sv_paused) = (m.STATE_DIR, m.PAUSED_FILE)
        m.STATE_DIR = st
        m.PAUSED_FILE = os.path.join(st, "paused.json")
        try:
            profs = ["/h/.codex", "/h/.codex-acct2", "/h/.codex-acct3"]
            for fn in ("engine_profiles", "worker_profiles"):
                setattr(m, fn, lambda e, _p=profs: _p)
            m.logged_in = lambda p, e: True
            m.cooling_down = lambda p: 0
            m.live_counts = lambda e: {p: 0 for p in profs}
            u = {profs[0]: 20.0, profs[1]: 5.0, profs[2]: 10.0}
            m.usage_window = lambda p, e: u[p]
            m.weekly_window = lambda p, e: 0.0
            check(not m.is_paused(profs[1]), "starts un-paused")
            check(
                m.pick_account("auto", engine="codex") == profs[1],
                "normally picks the freshest (acct2)",
            )
            m.set_paused(profs[1], True)
            check(
                m.is_paused(profs[1]) and m.load_paused() == {profs[1]},
                "pause persists to disk",
            )
            check(
                m.pick_account("auto", engine="codex") == profs[2],
                "paused acct2 is skipped -> work goes to the next-freshest (acct3) instead",
            )
            m.set_paused(profs[2], True)
            check(
                m.pick_account("auto", engine="codex") == profs[0],
                "with acct2+acct3 paused, only the unpaused acct1 is used",
            )
            m.set_paused(profs[0], True)
            raised = False
            try:
                m.pick_account("auto", engine="codex")
            except SystemExit:
                raised = True
            check(
                raised,
                "all accounts paused -> selection REFUSES (no task to a paused acct)",
            )
            check(
                m.pick_account("2", engine="codex") == profs[1],
                "explicit account selection still overrides a pause",
            )
            m.set_paused(profs[1], False)
            m.set_paused(profs[2], False)
            m.set_paused(profs[0], False)
            check(m.load_paused() == set(), "unpause clears the paused set")
            check(
                m.pick_account("auto", engine="codex") == profs[1],
                "after unpause, the freshest account is selected again",
            )
        finally:
            (m.STATE_DIR, m.PAUSED_FILE) = (sv_state, sv_paused)
            for k, v in saved_fns.items():
                setattr(m, k, v)


def test_solo_mode():
    """SOLO mode auto-rotation: when the solo session's current account hits >=97% 5h, rotate its
    auth IN PLACE to the freshest OTHER account and cool the maxed one until its 5h reset (not
    permanent). Below threshold -> no rotation. No fresh account -> waits, never strands."""
    print("test_solo_mode")
    keys = (
        "_solo_profile",
        "logged_in",
        "usage_window",
        "weekly_window",
        "_read_oauth_account",
        "fetch_claude_usage",
        "_native_claude_accounts",
        "_copy_claude_auth",
        "_swap_claude_auth",
        "_log_auth_rotation",
        "cooling_down",
        "set_cooldown",
        "clear_cooldown",
        "_cool_account",
        "_profile_recently_active",
    )
    saved = {k: getattr(m, k) for k in keys}
    with tempfile.TemporaryDirectory() as st:
        sv_state = m.STATE_DIR
        m.STATE_DIR = st
        try:
            solo = os.path.join(st, ".claude-solo")
            os.makedirs(solo)
            (natA, natB, natC) = ("/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3")
            (oa, five, wk) = ({}, {}, {})

            def reset_fleet(solo_uuid="A"):
                oa.clear()
                five.clear()
                wk.clear()
                base = {natA: ("A", "a@x"), natB: ("B", "b@x"), natC: ("C", "c@x")}
                for p, (u, e) in base.items():
                    oa[p] = {"accountUuid": u, "emailAddress": e}
                src = next((p for (p, (u, _)) in base.items() if u == solo_uuid))
                oa[solo] = dict(oa[src])
                five.update({natA: 50.0, natB: 10.0, natC: 5.0})
                five[solo] = five[src]
                wk.update({natA: 20.0, natB: 5.0, natC: 5.0})
                wk[solo] = wk[src]

            (cooled, acct_cooled, copied, swapped) = (set(), set(), [], [])
            busy = set()
            m._solo_profile = lambda: solo
            m._native_claude_accounts = lambda: [natA, natB, natC]
            m.logged_in = lambda p, e: True
            m.usage_window = lambda p, e: five[p]
            m.weekly_window = lambda p, e: wk[p]
            m._read_oauth_account = lambda p: oa.get(p, {})
            m.fetch_claude_usage = lambda p: {
                "five_hour": {"used_percent": five.get(p, 0), "resets_at": 4102444800},
                "seven_day": {"used_percent": wk.get(p, 0), "resets_at": 4102444800},
            }
            m._log_auth_rotation = lambda d: None
            m.cooling_down = lambda p: 1 if p in cooled else 0
            m.set_cooldown = lambda p, until: cooled.add(p)
            m.clear_cooldown = lambda p: cooled.discard(p)
            m._cool_account = lambda uuid, until: acct_cooled.add(uuid)
            m._profile_recently_active = lambda p, within_s=900: p in busy

            def fake_copy(dest, src):
                copied.append((dest, src))
                oa[dest] = dict(oa[src])
                five[dest] = five[src]
                wk[dest] = wk[src]
                return True

            def fake_swap(a, b):
                swapped.append((a, b))
                (oa[a], oa[b]) = (dict(oa[b]), dict(oa[a]))
                (five[a], five[b]) = (five[b], five[a])
                (wk[a], wk[b]) = (wk[b], wk[a])
                return True

            m._copy_claude_auth = fake_copy
            m._swap_claude_auth = fake_swap
            reset_fleet("A")
            five[solo] = 50.0
            msg = m.solo_rotate()
            check(
                "no rotation" in msg and (not copied) and (not swapped),
                "below 99% 5h AND 99% 7d -> no rotation",
            )
            reset_fleet("A")
            five[solo] = 99.0
            msg = m.solo_rotate()
            check(
                "ROTATED" in msg and "swapped places" in msg,
                "current >=99% 5h, idle -> swap places",
            )
            check(
                swapped == [(natA, natC)],
                "spent account's slot is swapped with the freshest (A<->C)",
            )
            check(
                copied == [(solo, natA)],
                "solo re-points at the now-fresh primary slot (natA holds C)",
            )
            check(
                oa[natC]["accountUuid"] == "A" and oa[natA]["accountUuid"] == "C",
                "after swap: spent A now lives in C's old slot, fresh C in A's slot (no duplicate native)",
            )
            check(
                natC in cooled and "A" in acct_cooled,
                "spent account cooled in its new slot (natC) AND by uuid (A)",
            )
            check(
                natA not in cooled,
                "the slot now holding the fresh account is usable (cooldown cleared)",
            )
            check("c@x" in msg, "rotation message names the new account")
            reset_fleet("A")
            five[solo] = 40.0
            wk[solo] = 99.0
            cooled.clear()
            acct_cooled.clear()
            copied.clear()
            swapped.clear()
            msg = m.solo_rotate()
            check(
                "ROTATED" in msg
                and "weekly" in msg.lower()
                and ("swapped places" in msg)
                and (swapped == [(natA, natC)]),
                "weekly >=99% swaps too (even with 5h headroom)",
            )
            check(
                "A" in acct_cooled,
                "the weekly-maxed account is cooled (until its weekly reset)",
            )
            reset_fleet("A")
            five[solo] = 99.0
            cooled.clear()
            acct_cooled.clear()
            copied.clear()
            swapped.clear()
            busy.add(natC)
            msg = m.solo_rotate()
            check(
                "ROTATED" in msg and (not swapped) and (copied == [(solo, natC)]),
                "fresh slot busy -> fall back to isolated copy (native untouched)",
            )
            check(
                natA in cooled, "in copy fallback the maxed account is cooled in place"
            )
            busy.clear()
            reset_fleet("C")
            five[solo] = 99.0
            wk[solo] = 20.0
            cooled.clear()
            acct_cooled.clear()
            copied.clear()
            swapped.clear()
            cooled.update({natA, natB})
            msg = m.solo_rotate()
            check(
                "NO fresher" in msg and (not copied) and (not swapped),
                "every other account cooled/maxed -> wait for a reset, never strand",
            )
        finally:
            m.STATE_DIR = sv_state
            for k, v in saved.items():
                setattr(m, k, v)


def test_pick_orch_account():
    """The provider-launcher orchestrator picker must NOT land on a weekly-exhausted
    (>=99%) or PAUSED account, and prefers the freshest (lowest 5h+weekly). Fix for: the old
    least-live picker grabbing a 98%-weekly / paused account as the orchestrator session."""
    print("test_pick_orch_account")
    saved_fns = {
        k: getattr(m, k)
        for k in (
            "engine_profiles",
            "logged_in",
            "usage_window",
            "weekly_window",
            "live_counts",
            "cooling_down",
            "is_paused",
            "live_orchestrators",
        )
    }
    with tempfile.TemporaryDirectory() as st:
        (sv_state, sv_paused) = (m.STATE_DIR, m.PAUSED_FILE)
        m.STATE_DIR = st
        m.PAUSED_FILE = os.path.join(st, "paused.json")
        try:
            profs = [
                "/h/.claude",
                "/h/.claude-acct2",
                "/h/.claude-acct3",
                "/h/.claude-acct4",
                "/h/.claude-acct5",
                "/h/.claude-acct6",
            ]
            five = {
                profs[0]: 17.0,
                profs[1]: 0.0,
                profs[2]: 0.0,
                profs[3]: 10.0,
                profs[4]: 0.0,
                profs[5]: 12.0,
            }
            seven = {
                profs[0]: 4.0,
                profs[1]: 98.0,
                profs[2]: 98.0,
                profs[3]: 100.0,
                profs[4]: 0.0,
                profs[5]: 0.0,
            }
            m.engine_profiles = lambda e: profs
            m.logged_in = lambda p, e: True
            m.cooling_down = lambda p: 0
            m.usage_window = lambda p, e: five[p]
            m.weekly_window = lambda p, e: seven[p]
            m.live_counts = lambda e: {p: 0 for p in profs}
            m.live_orchestrators = lambda: []
            m.is_paused = lambda p: os.path.abspath(p) in m.load_paused()
            picks = {m.pick_orch_account("claude") for _ in range(4)}
            check(
                picks <= {profs[4], profs[5]},
                "orchestrator picks the FRESHEST weekly account (0%), never the 98% ones",
            )
            check(
                profs[1] not in picks and profs[3] not in picks,
                "never picks a 98%-weekly or 100%-exhausted account",
            )
            m.set_paused(profs[4], True)
            m.set_paused(profs[5], True)
            check(
                m.pick_orch_account("claude") == profs[0],
                "paused fresh accounts skipped -> next-freshest (acct1, 4%) chosen",
            )
            m.set_paused(profs[0], True)
            check(
                m.pick_orch_account("claude") in (profs[1], profs[2]),
                "all fresh paused -> a <99% account is still usable (orchestrator must run somewhere)",
            )
            m.set_paused(profs[1], True)
            m.set_paused(profs[2], True)
            check(
                m.pick_orch_account("claude") is None,
                "every account paused/exhausted -> None (launcher falls back to ~/.claude)",
            )
        finally:
            (m.STATE_DIR, m.PAUSED_FILE) = (sv_state, sv_paused)
            for k, v in saved_fns.items():
                setattr(m, k, v)


def test_pick_orch_anti_stack():
    """Two interactive orchestrators must NOT stack on one account — they'd share Claude Code's
    per-account `/goal` slot and cross-contaminate (the reported incident). pick_orch_account
    avoids an account already hosting a live orchestrator, is engine-scoped, and still returns a
    least-bad account (never None) when every eligible account is occupied."""
    print("test_pick_orch_anti_stack")
    saved = {
        k: getattr(m, k)
        for k in (
            "engine_profiles",
            "logged_in",
            "usage_window",
            "weekly_window",
            "live_counts",
            "cooling_down",
            "is_paused",
            "live_orchestrators",
        )
    }
    with tempfile.TemporaryDirectory() as st:
        (sv_state, sv_paused) = (m.STATE_DIR, m.PAUSED_FILE)
        m.STATE_DIR = st
        m.PAUSED_FILE = os.path.join(st, "paused.json")
        try:
            profs = ["/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3"]
            m.engine_profiles = lambda e: profs
            m.logged_in = lambda p, e: True
            m.cooling_down = lambda p: 0
            m.is_paused = lambda p: False
            m.usage_window = lambda p, e: 0.0
            m.weekly_window = lambda p, e: 0.0
            m.live_counts = lambda e: {p: 0 for p in profs}

            def orch(account_dir, engine="claude", session="s"):
                return {
                    "session": session,
                    "engine": engine,
                    "account_dir": account_dir,
                    "live": True,
                }

            m.live_orchestrators = lambda: [orch(".claude", session="A")]
            picks = {m.pick_orch_account("claude") for _ in range(6)}
            check(
                profs[0] not in picks and picks <= {profs[1], profs[2]},
                "anti-stack: an account already hosting a live orchestrator is avoided",
            )
            m.live_orchestrators = lambda: [
                orch(".claude", engine="codex", session="C")
            ]
            check(
                m.pick_orch_account("claude") == profs[0],
                "anti-stack is engine-scoped (a codex orch doesn't block a claude pick)",
            )
            m.live_orchestrators = lambda: [
                orch(".claude", session="A"),
                orch(".claude-acct2", session="B"),
                orch(".claude-acct3", session="D"),
            ]
            check(
                m.pick_orch_account("claude") in profs,
                "all accounts occupied -> least-bad account, never None",
            )
        finally:
            (m.STATE_DIR, m.PAUSED_FILE) = (sv_state, sv_paused)
            for k, v in saved.items():
                setattr(m, k, v)


def test_orchestrators_on_account():
    """orchestrators_on_account: live orchestrators on a given config-dir — engine-scoped,
    excluding a named session, basename match (path-root-agnostic)."""
    print("test_orchestrators_on_account")
    sv = m.live_orchestrators
    try:
        m.live_orchestrators = lambda: [
            {
                "session": "A",
                "engine": "claude",
                "account_dir": ".claude",
                "live": True,
            },
            {
                "session": "B",
                "engine": "claude",
                "account_dir": ".claude",
                "live": True,
            },
            {
                "session": "C",
                "engine": "claude",
                "account_dir": ".claude-acct2",
                "live": True,
            },
            {"session": "D", "engine": "codex", "account_dir": ".claude", "live": True},
        ]
        a = m.orchestrators_on_account("/anywhere/.claude", "claude")
        check(
            {o["session"] for o in a} == {"A", "B"},
            "claude orchestrators on .claude (basename match)",
        )
        a2 = m.orchestrators_on_account(
            "/anywhere/.claude", "claude", exclude_session="A"
        )
        check(
            {o["session"] for o in a2} == {"B"},
            "exclude_session drops the named session",
        )
        a3 = m.orchestrators_on_account("/anywhere/.claude-acct2", "claude")
        check(
            {o["session"] for o in a3} == {"C"}, "scopes to the requested account_dir"
        )
        check(
            m.orchestrators_on_account("/anywhere/.claude-acct9", "claude") == [],
            "an account with no orchestrators returns []",
        )
    finally:
        m.live_orchestrators = sv


def test_colocation_banner():
    """colocation_banner warns (naming the /goal hazard) only when THIS session shares its
    account with another live orchestrator, and never raises on a degraded registry."""
    print("test_colocation_banner")
    sv = {
        k: getattr(m, k)
        for k in (
            "live_orchestrators",
            "current_orchestrator_profile",
            "current_orch_session",
        )
    }
    try:
        m.current_orchestrator_profile = lambda e=None: "/h/.claude"
        m.current_orch_session = lambda: "ME"
        m.live_orchestrators = lambda: [
            {
                "session": "ME",
                "engine": "claude",
                "account_dir": ".claude",
                "live": True,
            }
        ]
        check(
            m.colocation_banner("claude") == "", "no banner when alone on the account"
        )
        m.live_orchestrators = lambda: [
            {
                "session": "ME",
                "engine": "claude",
                "account_dir": ".claude",
                "live": True,
            },
            {
                "session": "OTHER",
                "engine": "claude",
                "account_dir": ".claude",
                "live": True,
                "account": 1,
                "project": "sample",
                "cwd": "/x/y",
            },
        ]
        b = m.colocation_banner("claude")
        check(
            bool(b) and "/goal" in b and ("SHARED-ACCOUNT" in b),
            "banner fires + names the /goal hazard when co-located",
        )
        m.live_orchestrators = lambda: [
            {
                "session": "OTHER",
                "engine": "claude",
                "account_dir": ".claude-acct2",
                "live": True,
            }
        ]
        check(
            m.colocation_banner("claude") == "",
            "no banner when the sibling is on another account",
        )

        def boom():
            raise RuntimeError("registry down")

        m.live_orchestrators = boom
        check(
            m.colocation_banner("claude") == "",
            "degraded registry -> '' (never raises)",
        )
    finally:
        for k, v in sv.items():
            setattr(m, k, v)


def test_dedicated_orchestrator():
    """The dedicated orchestrator account: discovered when set up, appended to
    engine_profiles (status/keepalive/usage cover it), labeled "orch", EXCLUDED from
    worker selection only when the session reserved it (--orchestrator), and never
    addressable by number. No orch account -> identical to before (fallback)."""
    print("test_dedicated_orchestrator")
    saved = {
        k: os.environ.get(k)
        for k in ("NEOMAX_CLAUDE_ORCH", "NEOMAX_ORCH_RESERVED", "NEOMAX_PROFILES")
    }
    try:
        with tempfile.TemporaryDirectory() as st:
            (p1, p2) = (os.path.join(st, "p1"), os.path.join(st, "p2"))
            op = os.path.join(st, "orch")
            for d in (p1, p2, op):
                os.makedirs(d)
            os.environ["NEOMAX_PROFILES"] = p1 + ":" + p2
            os.environ.pop("NEOMAX_CLAUDE_ORCH", None)
            os.environ.pop("NEOMAX_ORCH_RESERVED", None)
            saved_dir = m.ENGINES["claude"].get("orch_dir")
            m.ENGINES["claude"]["orch_dir"] = os.path.join(st, "no-such-orch")
            try:
                check(
                    m.orch_profile("claude") is None,
                    "no orch dir -> orch_profile None (fallback)",
                )
            finally:
                m.ENGINES["claude"]["orch_dir"] = saved_dir
            check(
                m.engine_profiles("claude") == [p1, p2], "no orch -> profiles unchanged"
            )
            os.environ["NEOMAX_CLAUDE_ORCH"] = op
            check(m.orch_profile("claude") == op, "orch profile discovered")
            check(
                m.engine_profiles("claude") == [p1, p2, op],
                "orch appended LAST to profiles",
            )
            check(
                m.profile_acct_no(op, "claude") == "orch",
                'orch profile numbered "orch"',
            )
            check(
                m.worker_profiles("claude") == [p1, p2, op],
                "unreserved: orch account joins the worker pool",
            )
            os.environ["NEOMAX_ORCH_RESERVED"] = "1"
            check(m.orch_reserved(), "NEOMAX_ORCH_RESERVED=1 -> reserved")
            check(
                m.worker_profiles("claude") == [p1, p2],
                "reserved: orch account EXCLUDED from the worker pool",
            )
            check(
                op in m.engine_profiles("claude"),
                "reserved: orch still tracked for status/keepalive/usage",
            )
            sv = (
                m.logged_in,
                m.cooling_down,
                m.usage_window,
                m.weekly_window,
                m.live_counts,
            )
            try:
                m.logged_in = lambda p, e="claude": True
                m.cooling_down = lambda p: 0
                m.usage_window = lambda p, e: 0.0
                m.weekly_window = lambda p, e: 0.0
                m.live_counts = lambda e: {}
                got = {m.pick_account("auto", engine="claude") for _ in range(4)}
                check(
                    op not in got,
                    "pick_account auto never lands on the reserved orch account",
                )
                check(
                    m.pick_account("2", engine="claude") == p2,
                    "numeric selection unchanged",
                )
                os.environ.pop("NEOMAX_ORCH_RESERVED", None)
                check(
                    m.pick_account("orch", engine="claude") == op,
                    'explicit "orch" selector reaches the orch account when NOT reserved',
                )
            finally:
                (
                    m.logged_in,
                    m.cooling_down,
                    m.usage_window,
                    m.weekly_window,
                    m.live_counts,
                ) = sv
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_weekly_limit_and_selection():
    print("test_weekly_limit_and_selection")
    check(m.is_weekly_limit({"limit_window": "seven_day"}), "seven_day -> weekly")
    check(m.is_weekly_limit({"limit_window": "weekly"}), "weekly -> weekly")
    check(
        not m.is_weekly_limit({"limit_window": "five_hour"}), "five_hour -> NOT weekly"
    )
    check(not m.is_weekly_limit({}), "no limit_window -> NOT weekly")
    profs = ["/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3"]
    m.engine_profiles = lambda e: profs
    m.logged_in = lambda p, e: True
    m.cooling_down = lambda p: 0
    m.live_counts = lambda e: {p: 0 for p in profs}
    m.usage_window = lambda p, e: 0.0
    wk = {profs[0]: 30.0, profs[1]: 99.0, profs[2]: 50.0}
    m.weekly_window = lambda p, e: wk[p]
    chosen = m.pick_account("auto", engine="claude")
    check(chosen != profs[1], "skips the near-weekly-maxed account (>=SEVEN_D_SKIP)")
    check(chosen == profs[0], "picks the account with the MOST weekly headroom")
    wk2 = {profs[0]: 97.0, profs[1]: 98.5, profs[2]: 96.0}
    m.weekly_window = lambda p, e: wk2[p]
    check(
        m.pick_account("auto", engine="claude") == profs[2],
        "all just under the 99 gate -> keeps running on the freshest (soft gate)",
    )
    m.weekly_window = lambda p, e: 99.0
    refused = False
    try:
        m.pick_account("auto", engine="claude")
    except SystemExit:
        refused = True
    check(refused, "all accounts >=99% weekly -> pool EXHAUSTED, selection refuses")
    m.weekly_window = lambda p, e: 0.0
    m.usage_window = lambda p, e: 99.0
    refused = False
    try:
        m.pick_account("auto", engine="claude")
    except SystemExit:
        refused = True
    check(
        refused,
        "all Claude accounts >=99% five-hour -> pool EXHAUSTED, selection refuses",
    )


def test_fleet_scope():
    print("test_fleet_scope")
    saved = os.environ.get("NEOMAX_FLEET")
    try:
        os.environ.pop("NEOMAX_FLEET", None)
        check(
            m.allowed_engines() == {"claude", "codex", "opencode", "kimi", "grok"},
            "default scope = all five engines",
        )
        os.environ["NEOMAX_FLEET"] = "codex"
        check(
            m.allowed_engines() == {"codex"}
            and m.engine_in_scope("codex")
            and (not m.engine_in_scope("claude")),
            "NEOMAX_FLEET=codex -> codex only",
        )
        os.environ["NEOMAX_FLEET"] = "claude"
        check(
            m.allowed_engines() == {"claude"} and (not m.engine_in_scope("codex")),
            "NEOMAX_FLEET=claude -> claude only",
        )
        os.environ["NEOMAX_FLEET"] = "opencode"
        check(
            m.allowed_engines() == {"opencode"}
            and m.engine_in_scope("opencode")
            and (not m.engine_in_scope("claude"))
            and (not m.engine_in_scope("codex")),
            "NEOMAX_FLEET=opencode -> OpenCode only",
        )
        os.environ["NEOMAX_FLEET"] = "kimi,codex"
        check(
            m.allowed_engines() == {"kimi", "codex"}
            and (not m.engine_in_scope("claude")),
            "comma-separated arbitrary worker pairing is enforced",
        )
        os.environ["NEOMAX_FLEET"] = "all"
        check(
            all(
                (
                    m.engine_in_scope(e)
                    for e in ("claude", "codex", "opencode", "kimi", "grok")
                )
            ),
            "NEOMAX_FLEET=all -> all five",
        )
    finally:
        if saved is None:
            os.environ.pop("NEOMAX_FLEET", None)
        else:
            os.environ["NEOMAX_FLEET"] = saved


def test_weekly_deadline_selection():
    """Account selection is 5h-FIRST with a GENTLE weekly-deadline nudge (use-it-or-lose-it). Single-
    account picks (rotate / solo / orchestrator launch + handoff) prefer the lowest-5h account, and
    only when 5h is ~equal does the nudge steer toward the account whose 7-day window resets SOONEST
    (burning quota about to reset rather than eating into one that resets days later). A 5h ceiling
    means we never land on an about-to-wall account. This REPLACES the old deadline-FIRST ordering,
    which collapsed accounts into one coarse bucket and converged every concurrent rotation onto the
    single soonest-resetting account (the herd that killed many sessions at once). Applies to BOTH
    engines; worker fan-out keeps even-spread primary with the deadline a gentle nudge."""
    print("test_weekly_deadline_selection")
    DAY = 86400
    H6 = 6 * 3600
    with tempfile.TemporaryDirectory() as ud:
        sv_usage = m.USAGE_DIR
        m.USAGE_DIR = ud
        try:
            now = time.time()

            def _write(base, engine, seven_resets, expired=False):
                fn = os.path.join(
                    ud,
                    ("claude-%s.json" if engine == "claude" else "codex-%s.json")
                    % base,
                )
                d = (
                    {"expired": True}
                    if expired
                    else {"seven_day": {"resets_at": seven_resets}}
                )
                with open(fn, "w") as f:
                    json.dump(d, f)

            _write(".claude-acct2", "claude", now + 2 * DAY)
            check(
                abs(
                    _real_weekly_reset_at("/h/.claude-acct2", "claude")
                    - (now + 2 * DAY)
                )
                < 5,
                "weekly_reset_at reads a future seven_day.resets_at from the usage cache",
            )
            _write(".claude-acct3", "claude", now - 3600)
            check(
                _real_weekly_reset_at("/h/.claude-acct3", "claude") is None,
                "weekly_reset_at -> None for an already-reset weekly window",
            )
            _write(".claude-acct4", "claude", None, expired=True)
            check(
                _real_weekly_reset_at("/h/.claude-acct4", "claude") is None,
                "weekly_reset_at -> None for an expired-token cache",
            )
            check(
                _real_weekly_reset_at("/h/.claude-acct9", "claude") is None,
                "weekly_reset_at -> None (never raises) when no cache file exists",
            )
            _write(".codex-acct2", "codex", now + 4 * DAY)
            check(
                abs(_real_weekly_reset_at("/h/.codex-acct2", "codex") - (now + 4 * DAY))
                < 5,
                "weekly_reset_at reads the Codex usage cache too — same rule for Codex accounts",
            )
        finally:
            m.USAGE_DIR = sv_usage
    keys = (
        "usage_window",
        "weekly_window",
        "engine_profiles",
        "worker_profiles",
        "logged_in",
        "is_paused",
        "live_counts",
        "cooling_down",
        "_native_claude_accounts",
        "weekly_reset_at",
    )
    saved = {k: getattr(m, k) for k in keys}
    sv_state = m.STATE_DIR
    _state_td = tempfile.mkdtemp()
    m.STATE_DIR = _state_td
    try:
        now = time.time()
        (five, week, reset) = ({}, {}, {})
        m.usage_window = lambda p, e=None: five.get(p, 0.0)
        m.weekly_window = lambda p, e=None: week.get(p, 0.0)
        m.weekly_reset_at = lambda p, e=None: reset.get(p)
        m.logged_in = lambda p, e=None: True
        m.is_paused = lambda p: False
        m.cooling_down = lambda p: 0
        m.live_counts = lambda e: {}
        (A, B, C) = ("/h/.claude", "/h/.claude-acct2", "/h/.claude-acct3")
        reset.update({A: now + 2 * DAY + H6, B: now + 5 * DAY + H6, C: None})
        check(
            m.weekly_deadline_tier(A, "claude") < m.weekly_deadline_tier(B, "claude"),
            "weekly_deadline_tier: a sooner reset is a lower (more urgent) tier",
        )
        check(
            m.weekly_deadline_tier(C, "claude")
            == int(m.WEEKLY_HORIZON_S / m.WEEKLY_BUCKET_S),
            "weekly_deadline_tier: unknown/no reset -> the far horizon tier (no deadline pressure)",
        )
        five.update({A: 20.0, B: 20.0, C: 20.0})
        week.update({A: 0, B: 0, C: 0})
        reset.update(
            {A: now + 2 * DAY + H6, B: now + 5 * DAY + H6, C: now + 6 * DAY + H6}
        )
        check(
            min([A, B, C], key=lambda p: m.rotation_rank(p, "claude")) == A,
            "rotation_rank: with equal 5h, the soonest-resetting account is preferred (A ~2d)",
        )
        five.update({A: 5.0, B: 40.0, C: 45.0})
        reset.update(
            {A: now + 6 * DAY + H6, B: now + 1 * DAY + H6, C: now + 2 * DAY + H6}
        )
        check(
            min([A, B, C], key=lambda p: m.rotation_rank(p, "claude")) == A,
            "rotation_rank: a much-fresher 5h account wins even though it resets latest (5h-first)",
        )
        five.update({A: 20.0, B: 21.0, C: 20.5})
        reset.update(
            {A: now + 5 * DAY + H6, B: now + 1 * DAY + H6, C: now + 6 * DAY + H6}
        )
        check(
            min([A, B, C], key=lambda p: m.rotation_rank(p, "claude")) == B,
            "rotation_rank: with near-equal 5h the deadline nudge prefers the soonest reset (B)",
        )
        five.update({A: 96.0, B: 30.0, C: 10.0})
        reset.update(
            {A: now + 1 * DAY + H6, B: now + 3 * DAY + H6, C: now + 6 * DAY + H6}
        )
        check(
            min([A, B, C], key=lambda p: m.rotation_rank(p, "claude")) == C,
            "rotation_rank: a 5h-ceiling account (A 96%) is skipped; among the rest freshest 5h (C) wins",
        )
        m._native_claude_accounts = lambda: [A, B, C]
        five.update({A: 15.0, B: 15.0, C: 15.0})
        week.update({A: 0, B: 0, C: 0})
        reset.update(
            {A: now + 5 * DAY + H6, B: now + 2 * DAY + H6, C: now + 6 * DAY + H6}
        )
        check(
            m.solo_pick_freshest() == B,
            "solo_pick_freshest: equal 5h -> the soonest-resetting account (B ~2d)",
        )
        m.engine_profiles = lambda e: [A, B, C]
        five.update({A: 10.0, B: 10.0, C: 10.0})
        week.update({A: 0, B: 0, C: 0})
        reset.update(
            {A: now + 6 * DAY + H6, B: now + 2 * DAY + H6, C: now + 5 * DAY + H6}
        )
        check(
            m.pick_orch_account("claude") == B,
            "pick_orch_account: equal 5h -> the soonest-resetting account (B)",
        )
        five.update({A: 10.0, B: 97.0, C: 10.0})
        check(
            m.pick_orch_account("claude") == C,
            "pick_orch_account: skips a 5h-ceiling account (B 97%) -> next soonest reset (C) over far A",
        )
        five.update({A: 20.0, B: 20.0, C: 20.0})
        week.update({A: 10.0, B: 10.0, C: 10.0})
        reset.update(
            {A: now + 5 * DAY + H6, B: now + 6 * DAY + H6, C: now + 2 * DAY + H6}
        )
        check(
            m.pick_freshest_account("claude") == C,
            "pick_freshest_account: the soonest-resetting headroom account (C ~2d) is the handoff target",
        )
        m.worker_profiles = lambda e: [A, B, C]
        week.update({A: 0, B: 0, C: 0})
        five.update({A: 30.0, B: 30.0, C: 30.0})
        reset.update(
            {A: now + 5 * DAY + H6, B: now + 2 * DAY + H6, C: now + 6 * DAY + H6}
        )
        check(
            m.pick_account("auto", engine="claude") == B,
            "pick_account: equal 5h -> the gentle weekly-deadline nudge picks the soonest-resetting account (B)",
        )
        five.update({A: 5.0, B: 30.0, C: 30.0})
        reset.update(
            {A: now + 6 * DAY + H6, B: now + 2 * DAY + H6, C: now + 2 * DAY + H6}
        )
        check(
            m.pick_account("auto", engine="claude") == A,
            "pick_account: a much-fresher account still wins (the deadline nudge does NOT dominate even-spread)",
        )
    finally:
        m.STATE_DIR = sv_state
        import shutil as _sh

        _sh.rmtree(_state_td, ignore_errors=True)
        for k, v in saved.items():
            setattr(m, k, v)
