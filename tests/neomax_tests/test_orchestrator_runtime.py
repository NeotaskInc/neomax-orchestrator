"""Concurrent orchestrators, turn hooks, orientation, modes, and hardening."""

from .support import *


def test_orchestrator_coordination():
    """Cross-orchestrator coordination: the registry tracks every concurrent Neomax provider session
    (live = pid alive, GCs dead+old), runs are owned by their launching session, and the
    destructive guard refuses to act on ANOTHER live orchestrator's run without --any —
    while a bare CLI call (no NEOMAX_ORCH_SESSION) keeps acting on everything (unchanged)."""
    print("test_orchestrator_coordination")
    import io, contextlib, sys as _sys

    saved_env = {
        k: os.environ.get(k)
        for k in ("NEOMAX_ORCH_SESSION", "NEOMAX_ORCH_PID", "NEOMAX_ROLE")
    }
    with tempfile.TemporaryDirectory() as st:
        (sv_state, sv_orch) = (m.STATE_DIR, m.ORCH_DIR)
        m.STATE_DIR = st
        m.ORCH_DIR = os.path.join(st, "orchestrators")
        try:
            mypid = os.getpid()
            m.register_orchestrator("orch-A", mypid, "claude")
            m.register_orchestrator("orch-B", 2147480000, "codex")
            allo = m.all_orchestrators()
            live = m.live_orchestrators()
            check(
                len(allo) == 2 and {o["session"] for o in live} == {"orch-A"},
                "registry: both registered; only the live-pid one is 'live'",
            )
            started0 = next((o["started"] for o in allo if o["session"] == "orch-A"))
            m.register_orchestrator("orch-A", mypid, "claude")
            check(
                next(
                    (
                        o["started"]
                        for o in m.all_orchestrators()
                        if o["session"] == "orch-A"
                    )
                )
                == started0,
                "re-register preserves 'started'",
            )
            os.environ["NEOMAX_ORCH_SESSION"] = "orch-B"
            recA = {"id": "rA", "orch_session": "orch-A", "status": "done"}
            recB = {"id": "rB", "orch_session": "orch-B", "status": "done"}
            recLegacy = {"id": "rL", "status": "done"}
            check(
                m.owned_by_other_live_orch(recA)
                and (not m.owned_by_other_live_orch(recB))
                and (not m.owned_by_other_live_orch(recLegacy)),
                "owned_by_other_live_orch: other-live=True, mine=False, legacy=False",
            )

            def guard(rec, argv):
                try:
                    m.refuse_if_other_orch(rec, "clean", argv)
                    return "passed"
                except SystemExit as e:
                    return "refused(%s)" % e.code

            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                r1 = guard(recA, ["rA"])
                r2 = guard(recA, ["rA", "--any"])
                r3 = guard(recB, ["rB"])
            check(
                r1 == "refused(3)",
                "destructive op on another live orch's run is REFUSED",
            )
            check(r2 == "passed", "--any overrides the refusal")
            check(r3 == "passed", "acting on my OWN run is allowed")
            os.environ["NEOMAX_ORCH_SESSION"] = "orch-A"
            recDead = {"id": "rD", "orch_session": "orch-B", "status": "done"}
            check(
                not m.owned_by_other_live_orch(recDead),
                "a DEAD orchestrator's runs are not protected (cleanable)",
            )
            os.environ.pop("NEOMAX_ORCH_SESSION", None)
            check(
                not m.owned_by_other_live_orch(recA),
                "bare CLI (no orch session) is never blocked from any run",
            )
            check(
                m.current_orch_session() is None,
                "current_orch_session None for a bare call",
            )
            p = m._orch_path("orch-old")
            os.makedirs(m.ORCH_DIR, exist_ok=True)
            json.dump(
                {
                    "session": "orch-old",
                    "pid": 2147480001,
                    "engine": "claude",
                    "last_seen": 0,
                    "started": 0,
                },
                open(p, "w"),
            )
            m.all_orchestrators()
            check(
                not os.path.exists(p),
                "dead + ancient orchestrator entry is GC'd on listing",
            )
            m.unregister_orchestrator("orch-A")
            check(
                "orch-A" not in {o["session"] for o in m.all_orchestrators()},
                "unregister removes the entry",
            )
            open(os.path.join(m.ORCH_DIR, "junk.json"), "w").write("{ broken")
            m.all_orchestrators()
            check(True, "malformed orchestrator file doesn't crash all_orchestrators()")
        finally:
            (m.STATE_DIR, m.ORCH_DIR) = (sv_state, sv_orch)
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_turn_hook_preserves_model_choice():
    print("test_turn_hook_preserves_model_choice")
    import contextlib, io

    keys = ("NEOMAX_ROLE", "NEOMAX_MODE", "NEOMAX_WORKER")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        with tempfile.TemporaryDirectory() as st:
            settings = os.path.join(st, "settings.json")
            json.dump({"model": "claude-opus-5[1m]", "keep": 1}, open(settings, "w"))
            os.environ["CLAUDE_CONFIG_DIR"] = st
            os.environ.pop("NEOMAX_ROLE", None)
            os.environ.pop("NEOMAX_MODE", None)
            os.environ.pop("NEOMAX_WORKER", None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                m.cmd_turn_hook([])
            check(out.getvalue() == "", "plain sessions receive no Neomax turn context")
            check(
                json.load(open(settings))["model"] == "claude-opus-5[1m]",
                "an explicit user model is never changed or warned about",
            )
            os.environ["NEOMAX_ROLE"] = "claude"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                m.cmd_turn_hook([])
            payload = json.loads(out.getvalue())
            check(
                "ORCHESTRATOR MODE"
                in payload["hookSpecificOutput"]["additionalContext"]
                and "systemMessage" not in payload,
                "orchestrator turn hook adds fleet context without a model warning",
            )
            check(
                json.load(open(settings)) == {"model": "claude-opus-5[1m]", "keep": 1},
                "turn hook leaves all Claude settings untouched",
            )
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_orchestrator_turn_reminder():
    """The per-turn orchestrator status line (UserPromptSubmit additionalContext). It is
    Informational only: live worker/account counts + a pointer to the
    project's CLAUDE.md / AGENTS.md for strategy — the old DELEGATE-don't-execute mandates
    made the orchestrator too aggressive and are gone. It must self-gate EXACTLY like orient
    --hook (silent for worker / cmax-solo / non-orchestrator sessions), reflect real worker
    counts from the runs registry, and NEVER raise."""
    print("test_orchestrator_turn_reminder")
    import io, contextlib, sys as _sys

    keys = (
        "NEOMAX_ROLE",
        "NEOMAX_MODE",
        "NEOMAX_FLEET",
        "NEOMAX_WORKER",
        "NEOMAX_PROFILES",
        "NEOMAX_CODEX_PROFILES",
        "NEOMAX_OPENCODE_PROFILES",
        "NEOMAX_KIMI_PROFILES",
        "NEOMAX_GROK_PROFILES",
    )
    saved = {k: os.environ.get(k) for k in keys}
    sv_runs = m.RUNS_DIR
    try:
        with tempfile.TemporaryDirectory() as st:
            runs = os.path.join(st, "runs")
            os.makedirs(runs)
            m.RUNS_DIR = runs
            os.environ["NEOMAX_PROFILES"] = ":".join(
                (os.path.join(st, "c%d" % i) for i in range(3))
            )
            os.environ["NEOMAX_CODEX_PROFILES"] = ":".join(
                (os.path.join(st, "x%d" % i) for i in range(2))
            )
            os.environ["NEOMAX_OPENCODE_PROFILES"] = os.path.join(st, "o0")
            os.environ["NEOMAX_KIMI_PROFILES"] = os.path.join(st, "k0")
            os.environ["NEOMAX_GROK_PROFILES"] = os.path.join(st, "g0")
            os.environ["NEOMAX_FLEET"] = "all"
            os.environ["NEOMAX_ROLE"] = "claude"
            os.environ.pop("NEOMAX_MODE", None)
            os.environ.pop("NEOMAX_WORKER", None)
            r = m._orchestrator_turn_reminder()
            check(
                bool(r) and "ORCHESTRATOR MODE" in r,
                "orchestrator session -> reminder emitted",
            )
            check(
                "0 worker(s) running" in r and "8 of 8 in-scope account(s) idle" in r,
                "0 active workers -> neutral live counts",
            )
            check(
                "3 Claude + 2 Codex + 1 OpenCode + 1 Kimi + 1 Grok" in r,
                "live line names every in-scope engine/account count",
            )
            check(
                "SOLO" not in r
                and "STOP" not in r
                and ("--engine codex" not in r)
                and ("Do NOT" not in r),
                "reminder is a status line, not a mandate",
            )
            check(
                "neomax status" in r and "neomax help" in r,
                "reminder points at live status + the command registry",
            )
            json.dump(
                {
                    "id": "R1",
                    "status": "running",
                    "pid": os.getpid(),
                    "profile": os.path.join(st, "c0"),
                },
                open(os.path.join(runs, "R1.json"), "w"),
            )
            r2 = m._orchestrator_turn_reminder()
            check(
                "1 worker(s) running across 1 account(s)" in r2
                and "7 of 8 in-scope account(s) idle" in r2,
                "live counts reflect the running worker (1 busy, 7 idle of 8)",
            )
            os.environ["NEOMAX_WORKER"] = "1"
            check(m._orchestrator_turn_reminder() is None, "delegated worker -> silent")
            os.environ.pop("NEOMAX_WORKER", None)
            os.environ["NEOMAX_MODE"] = "solo"
            check(
                m._orchestrator_turn_reminder() is None, "cmax solo session -> silent"
            )
            os.environ.pop("NEOMAX_MODE", None)
            os.environ.pop("NEOMAX_ROLE", None)
            check(
                m._orchestrator_turn_reminder() is None,
                "non-orchestrator session -> silent",
            )
            os.environ["NEOMAX_ROLE"] = "codex"
            os.environ["NEOMAX_FLEET"] = "codex"
            r6 = m._orchestrator_turn_reminder()
            check(
                bool(r6) and "2 Codex" in r6, "codex-only scope -> single-engine counts"
            )
            os.environ["NEOMAX_ROLE"] = "claude"
            os.environ["NEOMAX_FLEET"] = "all"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                m.cmd_turn_hook([])
            out = json.loads(buf.getvalue().strip())
            hso = out.get("hookSpecificOutput", {})
            check(
                hso.get("hookEventName") == "UserPromptSubmit"
                and "ORCHESTRATOR MODE" in hso.get("additionalContext", ""),
                "turn hook injects the reminder as UserPromptSubmit additionalContext",
            )
            check(
                "systemMessage" not in out,
                "turn hook never warns about model selection",
            )
    finally:
        m.RUNS_DIR = sv_runs
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_hardening_regressions():
    """Final-pass hardening: area locks, archive pending-spill, and payload caps."""
    print("test_hardening_regressions")
    import time as _t

    with tempfile.TemporaryDirectory() as st:
        saved_locks = m.LOCKS_DIR
        try:
            m.LOCKS_DIR = os.path.join(st, "locks")
            repo = "/repo/x"
            check(m.acquire_area_lock(repo, "src", "r1"), "specific area acquired")
            check(
                not m.acquire_area_lock(repo, "*", "r2"),
                "global '*' refused while a specific area is held (atomic)",
            )
            m.release_area_locks(repo, ["src"], "r1")
            check(m.acquire_area_lock(repo, "*", "r3"), "'*' acquired once area freed")
            check(
                not m.acquire_area_lock(repo, "src", "r4"),
                "specific area refused while '*' held",
            )
            m.release_area_locks(repo, ["*"], "r3")
        finally:
            m.LOCKS_DIR = saved_locks
        saved = (m.HISTORY_PENDING, m.history_conn)
        try:
            m.HISTORY_PENDING = os.path.join(st, "pending")
            import sqlite3 as _sq

            def locked_conn():
                raise _sq.OperationalError("database is locked")

            class FakeC:
                def execute(self, *a, **k):
                    raise _sq.OperationalError("locked")

                def commit(self):
                    pass

                def close(self):
                    pass

            m.history_conn = lambda: FakeC()
            m.archive_run(
                {
                    "id": "20990101-000000-9",
                    "engine": "claude",
                    "profile": "/x",
                    "status": "done",
                }
            )
            check(
                os.path.isfile(
                    os.path.join(m.HISTORY_PENDING, "20990101-000000-9.json")
                ),
                "DB-locked archive spills the run to history-pending (never lost)",
            )
        finally:
            (m.HISTORY_PENDING, m.history_conn) = saved


def test_orient_directive():
    """The SessionStart opener is engine/mode-dynamic (no hardcoded count) and self-gates to
    interactive orchestrator sessions (not plain claude, not neomax workers)."""
    print("test_orient_directive")
    import io, contextlib

    keys = ("NEOMAX_ROLE", "NEOMAX_FLEET", "NEOMAX_PROJECT_ROOT", "NEOMAX_WORKER")
    saved = {k: os.environ.get(k) for k in keys}
    _cwd0 = os.getcwd()
    _outside = tempfile.mkdtemp()
    os.chdir(_outside)

    def setenv(**kw):
        for k in keys:
            os.environ.pop(k, None)
        for k, v in kw.items():
            if v is not None:
                os.environ[k] = v

    def hookout():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.cmd_orient(["--hook"])
        return buf.getvalue().strip()

    try:
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="all")
        t = m.orient_directive()
        check(
            "Claude Fable 5" in t and "Claude + Codex" in t,
            "claude orch / all -> Fable 5 + all pools",
        )
        check(
            "VERIFY which are actually authenticated" in t,
            "defers the LIVE fleet count to neomax status",
        )
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="codex")
        check(
            "Codex only" in m.orient_directive(),
            "claude orch / codex scope -> Codex-only workers",
        )
        setenv(NEOMAX_ROLE="codex", NEOMAX_FLEET="codex")
        t3 = m.orient_directive()
        check("Codex gpt-5.6-sol" in t3, "codex orch -> Codex gpt-5.6-sol orchestrator")
        check(
            "cdxmax resume" in t3 and "/login adopts" not in t3,
            "codex orient teaches the codex rotation adoption path (resume, not /login)",
        )
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="all")
        t4 = m.orient_directive()
        for banned in (
            "PRIME DIRECTIVE",
            "MAXIMAL FLEET FAN-OUT",
            "ENGINE ROUTING",
            "DELEGATE WITH FULL BRIEFS",
            "fifty concurrent",
            "~10 SUB-AGENTS",
            "DON'T AUTO-PILOT",
            "ACT ON JUDGMENT",
            "DELIVERY CONTRACT",
        ):
            check(banned not in t4, "lean opener: no built-in mandate %r" % banned)
        check(
            "CLAUDE.md / AGENTS.md" in t4,
            "opener defers orchestration strategy to the project's own MD files",
        )
        check(
            "YOUR TOOLBOX" in t4 and "neomax help" in t4 and ("neomax modes" in t4),
            "opener is a toolbox + points at the full command registry",
        )
        for cap in (
            "Agent tool",
            "run-all",
            "reconcile",
            "resume",
            "retry",
            "kill",
            "pause",
            "orchestrators",
            "neomax rotate",
            "handoff",
            "rotate-auth",
            "--engine codex",
            "--codex-model",
            "task add",
            "issue",
            "usage",
            "portal",
            "--goal",
            "--plan",
            "--pr",
        ):
            check(cap in t4, "toolbox names the %r capability" % cap)
        for command in (
            "neomax resume",
            "neomax retry",
            "neomax kill",
            "neomax orchestrators",
            "neomax handoff",
            "neomax rotate-auth",
            "neomax task add",
            "neomax issue open",
            "neomax usage",
        ):
            check(command in t4, "toolbox gives the exact %r invocation" % command)
        check(
            "run in parallel" in t4 and "EVENLY" in t4,
            "opener states the one principle: parallel work spread evenly across accounts",
        )
        check(
            "No target or limit on how many" in t4,
            "opener explicitly declines to set a worker/sub-agent count",
        )
        check("LAUNCH MODES" in t4, "opener carries the launch-mode map")
        for mo in m.ORCH_MODES:
            check(mo["cmd"] in t4, "opener names the %r launch command" % mo["cmd"])
        check(
            "cmax --workers codex" in t4
            and "cmax --workers claude" in t4
            and ("cdxmax" in t4)
            and ("cmax solo" in t4),
            "the scope/engine modes are all present verbatim",
        )
        check(
            "FIXED for its lifetime" in t4,
            "opener states the launch scope cannot change mid-session",
        )
        check(
            "neomax pause all --engine claude" in t4
            and "--engine codex` on every dispatch" in t4,
            "opener gives the two LIVE ways to narrow to one engine mid-session",
        )
        check("unpause" in t4, "opener says how to restore a paused engine")
        import re as _re

        for n in _re.findall(
            "\\b\\d+\\b", t4.split("HOW TO USE IT:")[1].split("Orient:")[0]
        ):
            check(False, "operating principle must contain NO numbers (found %r)" % n)
        check(True, "operating principle carries no worker-count numbers")
        check(
            "/login adopts the fresh account instantly" in t4,
            "claude orient teaches the claude rotation adoption path",
        )
        check(
            ">=99%" in t4 and "5-hour window" in t4 and ("weekly window" in t4),
            "claude opener documents the 99% 5h / 99% weekly auto-rotation",
        )
        setenv(NEOMAX_ROLE="codex", NEOMAX_FLEET="codex")
        tcx = m.orient_directive()
        check(
            "no 5-hour limit" in tcx and ">=99% of the weekly window" in tcx,
            "codex opener documents WEEKLY-ONLY rotation",
        )
        check(
            "5-hour window" not in tcx.split("ROTATION (automatic)")[1].split("\n")[0],
            "codex opener never claims a 5h rotation trigger",
        )
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="all")
        check(
            "PUSH branches + OPEN PRs freely, but NEVER" in t4
            and "merge to main without operator approval" in t4,
            "git safety kept: push/PR freely, merge to main requires operator approval",
        )
        check(
            "Fable 5" in t4 and "gpt-5.6" in t4,
            "model defaults kept (Fable 5 / gpt-5.6)",
        )
        check(
            "neomax help" in t4
            and "neomax task list" in t4
            and ("neomax reconcile" in t4),
            "opener keeps the mechanics: toolbox, durable task backlog, reconcile/ls/status",
        )
        check("%%" not in t4, "opener renders single % (no %% leak)")
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="codex")
        check(
            "Codex only" in m.orient_directive(),
            "claude orch + codex-only pool -> scope stated (no routing mandates)",
        )
        cwd0 = os.getcwd()
        with tempfile.TemporaryDirectory() as outside:
            try:
                os.chdir(outside)
                tm = m.orient_directive()
            finally:
                os.chdir(cwd0)
        check(
            "MULTI-PROJECT mode" in tm and "/project" in tm,
            "outside any project root -> MULTI-PROJECT orient listing all projects + the switch method",
        )
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="all")
        sv_proj = m.project_of
        sv_load = m.load_projects
        m.project_of = lambda p=None: "sample"
        m.load_projects = lambda: {
            "sample": {
                "root": "/workspace/sample",
                "repos": ["."],
                "branch_prefix": "samp",
                "brain": "CLAUDE.md",
                "agents": "AGENTS.md",
                "planning": "docs/neomax-orchestrator",
                "harness_brain": "project/private.local.md",
            }
        }
        try:
            tn = m.orient_directive()
        finally:
            m.project_of = sv_proj
            m.load_projects = sv_load
        check(
            "project/private.local.md" in tn and "/workspace/sample/CLAUDE.md" in tn,
            "generic orient honors an ignored local harness brain",
        )
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="all")
        out = hookout()
        check(
            out.startswith("{") and "additionalContext" in out,
            "--hook EMITS in an orchestrator session",
        )
        setenv()
        check(
            hookout() == "",
            "--hook SILENT for a plain (non-orchestrator) claude session",
        )
        setenv(NEOMAX_ROLE="claude", NEOMAX_FLEET="all", NEOMAX_WORKER="1")
        check(hookout() == "", "--hook SILENT inside a neomax worker")
    finally:
        os.chdir(_cwd0)
        try:
            os.rmdir(_outside)
        except OSError:
            pass
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_modes_quick_actions():
    """`neomax modes --json` is the ONE source the dashboard Quick-Actions modal renders.
    It must be valid JSON, list the launch modes (incl. the solo mode + its section) and the
    account commands (incl. the /rotate session-rotation), so the panel never drifts from the CLI."""
    print("test_modes_quick_actions")
    import io, contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.cmd_modes(["--json"])
    data = json.loads(buf.getvalue())
    modes = {x["id"]: x for x in data.get("modes", [])}
    cmds = " || ".join((c.get("cmd", "") for c in data.get("account_commands", [])))
    check(
        every(
            lambda i: i in modes,
            ["neomax-auto", "claude-all", "claude-codex", "codex-codex", "claude-solo"],
        ),
        "modes include Neomax automatic selection, provider-pinned modes, and solo mode",
    )
    check(
        modes["neomax-auto"]["cmd"] == "neomax"
        and "eligible connected provider"
        in modes["neomax-auto"]["orchestrator"].lower(),
        "plain neomax is the primary dynamic orchestration mode",
    )
    check(
        modes["claude-solo"]["cmd"].startswith("cmax solo")
        and "neomax" in modes["claude-solo"]["why"].lower(),
        "solo mode launches `cmax solo` and notes it does NOT delegate via neomax",
    )
    check(
        modes["claude-solo"].get("section")
        and "solo" in modes["claude-solo"]["section"].lower()
        and ("section" not in modes["claude-all"]),
        "solo carries its own section header; orchestrator modes default (grouped separately)",
    )
    check(
        "/rotate" in cmds and "neomax rotate" in cmds,
        "account commands surface universal provider rotation",
    )
    check(
        "pause" in cmds and "unpause" in cmds,
        "account commands surface live pause/unpause",
    )
    check("--swap" in cmds, "rotate-auth surfaces the place-swap (no-duplicate) option")
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        m.cmd_modes([])
    out = buf2.getvalue()
    check(
        "cmax solo" in out and "Start a solo session" in out,
        "plain `neomax modes` shows the solo section",
    )
