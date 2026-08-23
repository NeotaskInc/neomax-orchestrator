"""Conflict resolution, area locks, scheduler validation, healing, queues, and tidy."""

from .support import *


def test_union_resolve():
    """union_resolve: 2-way conflicts -> ordered dedup union; any diff3 base section
    ('|||||||') or malformed/unterminated block -> None (refuse, never corrupt)."""
    print("test_union_resolve")
    two = (
        "head\n<<<<<<< HEAD\nalpha\nshared\n=======\nbeta\nshared\n>>>>>>> theirs\ntail"
    )
    check(
        m.union_resolve(two) == "head\nalpha\nshared\nbeta\ntail",
        "2-way conflict -> union of both sides, order kept, dupes dropped",
    )
    check(
        m.union_resolve("plain\nlines") == "plain\nlines",
        "no markers -> text unchanged",
    )
    d3 = "x\n<<<<<<< HEAD\na\n||||||| base\no\n=======\nb\n>>>>>>> theirs\ny"
    check(
        m.union_resolve(d3) is None,
        "diff3 base section -> None (refuses to auto-resolve)",
    )
    check(
        m.union_resolve("||||||| merged common ancestors\nz") is None,
        "diff3 marker on the FIRST line -> None",
    )
    check(
        m.union_resolve("a\n<<<<<<< HEAD\nx\n=======\ny\n") is None,
        "unterminated conflict block -> None",
    )
    check(
        m.union_resolve("a\n<<<<<<< HEAD\nx\nno-separator") is None,
        "conflict with no ======= -> None",
    )


def test_area_lock_stale_reclaim():
    """acquire_area_lock reclaims a stale lock (terminal/dead/torn holder) instead of
    deadlocking, but never steals from a LIVE holder."""
    print("test_area_lock_stale_reclaim")
    import time as _t

    saved = (m.LOCKS_DIR, m.RUNS_DIR)
    try:
        with tempfile.TemporaryDirectory() as st:
            m.LOCKS_DIR = os.path.join(st, "locks")
            m.RUNS_DIR = os.path.join(st, "runs")
            os.makedirs(m.RUNS_DIR)
            repo = os.path.join(st, "repo")
            os.makedirs(repo)
            with open(os.path.join(m.RUNS_DIR, "r-dead.json"), "w") as f:
                json.dump({"id": "r-dead", "status": "done"}, f)
            lp = m._lock_path(repo, "src")
            with open(lp, "w") as f:
                json.dump(
                    {"runid": "r-dead", "pid": os.getpid(), "ts": int(_t.time())}, f
                )
            check(
                m.acquire_area_lock(repo, "src", "r-new") is True,
                "terminal holder's lock reclaimed (no deadlock)",
            )
            check(
                json.load(open(lp))["runid"] == "r-new",
                "lock file now owned by the new run",
            )
            check(
                m.acquire_area_lock(repo, "src", "r-new") is True,
                "re-entrant acquire by the same run",
            )
            with open(os.path.join(m.RUNS_DIR, "r-live.json"), "w") as f:
                json.dump({"id": "r-live", "status": "running", "pid": os.getpid()}, f)
            lp2 = m._lock_path(repo, "other")
            with open(lp2, "w") as f:
                json.dump(
                    {"runid": "r-live", "pid": os.getpid(), "ts": int(_t.time())}, f
                )
            check(
                m.acquire_area_lock(repo, "other", "r-new") is False,
                "live holder's lock is NOT stolen",
            )
            lp3 = m._lock_path(repo, "torn")
            open(lp3, "w").close()
            check(
                m.acquire_area_lock(repo, "torn", "r-new") is True,
                "torn/empty lock file reclaimed",
            )
            lp4 = m._lock_path(repo, "ghost")
            with open(lp4, "w") as f:
                json.dump(
                    {"runid": "r-ghost", "pid": 2**22 + 12345, "ts": int(_t.time())}, f
                )
            check(
                m.acquire_area_lock(repo, "ghost", "r-new") is True,
                "unknown-run dead-pid holder reclaimed",
            )
    finally:
        (m.LOCKS_DIR, m.RUNS_DIR) = saved


def test_run_all_norm_keys_and_validation():
    """run-all _norm_keys: malformed area/depends_on (bare string, number in list,
    non-list garbage) is normalized — the scheduler runs to completion with no
    AttributeError/TypeError. Genuinely invalid plans fail FAST with a clean exit 2."""
    print("test_run_all_norm_keys_and_validation")
    import io, contextlib, signal as _sig

    saved = (m.WORKTREES_DIR, m.LOCKS_DIR, m.LOGS_DIR, m.EVENTS_DIR, m.pick_account)
    keys = (
        "NEOMAX_MAX_LIVE",
        "NEOMAX_MAX_STALL",
        "NEOMAX_POLL",
        "NEOMAX_FLEET",
        "NEOMAX_PROFILES",
        "NEOMAX_CODEX_PROFILES",
    )
    saved_env = {k: os.environ.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            (repo, _wt, g) = _mk_repo(tmp, branch="neomax/seed")
            m.WORKTREES_DIR = os.path.join(tmp, "wts")
            m.LOCKS_DIR = os.path.join(tmp, "locks")
            m.LOGS_DIR = os.path.join(tmp, "logs")
            m.EVENTS_DIR = os.path.join(tmp, "events")
            os.environ["NEOMAX_MAX_LIVE"] = "1"
            os.environ["NEOMAX_MAX_STALL"] = "1"
            os.environ["NEOMAX_POLL"] = "0"
            os.environ.pop("NEOMAX_FLEET", None)
            os.environ["NEOMAX_PROFILES"] = os.path.join(tmp, "p1")
            os.environ["NEOMAX_CODEX_PROFILES"] = os.path.join(tmp, "p2")

            def no_account(*a, **k):
                raise SystemExit(1)

            m.pick_account = no_account
            plan = {
                "repo": repo,
                "base": "main",
                "plan": "tnorm",
                "parts": [
                    {"id": "a", "prompt": "x", "area": "justastring"},
                    {"id": "b", "prompt": "y", "area": ["a", 123], "depends_on": "a"},
                    {"id": "c", "prompt": "z", "area": {"not": "a list"}},
                ],
            }
            pf = os.path.join(tmp, "plan.json")
            with open(pf, "w") as f:
                json.dump(plan, f)
            (out, eout) = (io.StringIO(), io.StringIO())
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(eout):
                m.cmd_run_all([pf])
            check(
                "PLAN tnorm: 0/3 integrated, 3 outstanding" in out.getvalue(),
                "malformed area/deps normalized — run-all completes cleanly (no AttributeError)",
            )
            check(
                "3 parts" in eout.getvalue(),
                "all 3 malformed-area parts passed validation",
            )
            for bad_parts, label in (
                (["just-a-string-part"], "non-object part"),
                ([{"id": "a", "prompt": "x", "depends_on": ["ghost"]}], "unknown dep"),
                (
                    [
                        {"id": "a", "prompt": "x", "depends_on": ["b"]},
                        {"id": "b", "prompt": "y", "depends_on": ["a"]},
                    ],
                    "dependency cycle",
                ),
            ):
                with open(pf, "w") as f:
                    json.dump(
                        {
                            "repo": repo,
                            "base": "main",
                            "plan": "tbad",
                            "parts": bad_parts,
                        },
                        f,
                    )
                code = None
                try:
                    with (
                        contextlib.redirect_stdout(io.StringIO()),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        m.cmd_run_all([pf])
                except SystemExit as e:
                    code = e.code
                check(code == 2, "run-all %s -> clean sys.exit(2)" % label)
    finally:
        (m.WORKTREES_DIR, m.LOCKS_DIR, m.LOGS_DIR, m.EVENTS_DIR, m.pick_account) = saved
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _sig.signal(_sig.SIGINT, _sig.default_int_handler)
        _sig.signal(_sig.SIGTERM, _sig.SIG_DFL)
        _sig.signal(_sig.SIGHUP, _sig.SIG_DFL)


def test_run_all_stall_and_no_record():
    """run-all failure containment: (a) a launched part whose run record NEVER appears
    (child died before writing it) is marked failed after the 30s grace window — area
    lock released, scheduler terminates (no eternal wedge); (b) the MAX_STALL guard:
    NEOMAX_MAX_STALL no-progress cycles -> pending parts marked blocked, clean stop.
    Hermetic: fake clock (sleep advances 31s so the hardcoded grace elapses), Popen +
    pick_account + load_run stubbed — nothing real launches."""
    print("test_run_all_stall_and_no_record")
    import io, contextlib, signal as _sig

    (real_time, real_sub) = (m.time, m.subprocess)
    saved = (
        m.WORKTREES_DIR,
        m.LOCKS_DIR,
        m.LOGS_DIR,
        m.EVENTS_DIR,
        m.pick_account,
        m.load_run,
        m.time,
        m.subprocess,
    )
    keys = (
        "NEOMAX_MAX_LIVE",
        "NEOMAX_MAX_STALL",
        "NEOMAX_POLL",
        "NEOMAX_FLEET",
        "NEOMAX_PROFILES",
        "NEOMAX_CODEX_PROFILES",
    )
    saved_env = {k: os.environ.get(k) for k in keys}

    class _Clock:
        def __init__(self):
            (self.t, self.sleeps) = (real_time.time(), 0)

        def time(self):
            return self.t

        def sleep(self, _s):
            self.sleeps += 1
            if self.sleeps > 50:
                raise RuntimeError("run-all wedged: scheduler never terminated")
            self.t += 31.0

        def __getattr__(self, name):
            return getattr(real_time, name)

    launches = []

    class _Sub:
        def Popen(self, args, **kw):
            launches.append(list(args))

            class _P:
                pid = 999999

            return _P()

        def __getattr__(self, name):
            return getattr(real_sub, name)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            (repo, _wt, g) = _mk_repo(tmp, branch="neomax/seed2")
            m.WORKTREES_DIR = os.path.join(tmp, "wts")
            m.LOCKS_DIR = os.path.join(tmp, "locks")
            m.LOGS_DIR = os.path.join(tmp, "logs")
            m.EVENTS_DIR = os.path.join(tmp, "events")
            os.environ["NEOMAX_MAX_LIVE"] = "1"
            os.environ["NEOMAX_MAX_STALL"] = "3"
            os.environ["NEOMAX_POLL"] = "0"
            os.environ.pop("NEOMAX_FLEET", None)
            prof = os.path.join(tmp, "p1")
            os.environ["NEOMAX_PROFILES"] = prof
            os.environ["NEOMAX_CODEX_PROFILES"] = os.path.join(tmp, "p2")
            pf = os.path.join(tmp, "plan.json")
            clock = _Clock()
            (m.time, m.subprocess) = (clock, _Sub())
            m.pick_account = lambda selector, exclude=(), engine="claude", bias=None: (
                prof
            )
            m.load_run = lambda rid: None
            with open(pf, "w") as f:
                json.dump(
                    {
                        "repo": repo,
                        "base": "main",
                        "plan": "tnorec",
                        "parts": [{"id": "p1", "prompt": "x", "area": ["src"]}],
                    },
                    f,
                )
            (out, eout) = (io.StringIO(), io.StringIO())
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(eout):
                m.cmd_run_all([pf])
            check(
                "FAILED to start (no run record after 30s)" in eout.getvalue(),
                "record-less part marked failed after the 30s grace window",
            )
            check(
                "PLAN tnorec: 0/1 integrated, 1 outstanding" in out.getvalue(),
                "scheduler terminates and reports the failed part (no wedge)",
            )
            check(
                len(launches) == 1 and clock.sleeps == 1,
                "part launched once, failed on the first post-grace check, never relaunched",
            )
            check(
                not os.path.exists(m._lock_path(repo, "src")),
                "failed part's area lock released",
            )
            launches.clear()
            clock2 = _Clock()
            m.time = clock2

            def no_account(*a, **k):
                raise SystemExit(1)

            m.pick_account = no_account
            with open(pf, "w") as f:
                json.dump(
                    {
                        "repo": repo,
                        "base": "main",
                        "plan": "tstall",
                        "parts": [{"id": "p1", "prompt": "x", "area": ["src"]}],
                    },
                    f,
                )
            (out, eout) = (io.StringIO(), io.StringIO())
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(eout):
                m.cmd_run_all([pf])
            check(
                "no progress for 3 cycles" in eout.getvalue(),
                "MAX_STALL guard trips after exactly NEOMAX_MAX_STALL cycles",
            )
            check(
                "p1(blocked)" in eout.getvalue()
                and "PLAN tstall: 0/1 integrated, 1 outstanding" in out.getvalue(),
                "stalled pending part marked blocked; scheduler stops cleanly",
            )
            check(
                launches == [] and clock2.sleeps == 2,
                "nothing launched; loop bounded at max_stall-1 sleeps (no busy-wedge)",
            )
    finally:
        (
            m.WORKTREES_DIR,
            m.LOCKS_DIR,
            m.LOGS_DIR,
            m.EVENTS_DIR,
            m.pick_account,
            m.load_run,
            m.time,
            m.subprocess,
        ) = saved
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _sig.signal(_sig.SIGINT, _sig.default_int_handler)
        _sig.signal(_sig.SIGTERM, _sig.SIG_DFL)
        _sig.signal(_sig.SIGHUP, _sig.SIG_DFL)


def test_reconcile_heal_dedup_and_age():
    """reconcile --heal buckets error->retry / interrupted->resume / orphaned->kill+
    resume, records each heal (a second pass does NOT re-dispatch), and ages off the
    most-recent activity: ENDED long ago = skipped, but STARTED long ago + ended
    recently IS healed (the ended-not-started fix)."""
    print("test_reconcile_heal_dedup_and_age")
    import io, contextlib, time as _t

    saved = (
        m.STATE_DIR,
        m.RUNS_DIR,
        m.LOGS_DIR,
        m.EVENTS_DIR,
        m.SELF_HEAL_FILE,
        m.HISTORY_PENDING,
        m.dispatch_detached,
        m.kill_worker_inline,
        m.worker_alive,
        m.pid_alive,
    )
    try:
        with tempfile.TemporaryDirectory() as st:
            m.STATE_DIR = st
            m.RUNS_DIR = os.path.join(st, "runs")
            os.makedirs(m.RUNS_DIR)
            m.LOGS_DIR = os.path.join(st, "logs")
            m.EVENTS_DIR = os.path.join(st, "events")
            m.SELF_HEAL_FILE = os.path.join(st, "self-heal.json")
            m.HISTORY_PENDING = os.path.join(st, "history-pending")
            now = int(_t.time())
            recs = [
                {
                    "id": "r-err",
                    "status": "error",
                    "engine": "claude",
                    "started": now - 100 * 3600,
                    "ended": now - 600,
                },
                {
                    "id": "r-int",
                    "status": "interrupted",
                    "started": now - 1000,
                    "ended": now - 500,
                },
                {
                    "id": "r-orph",
                    "status": "running",
                    "pid": 4242,
                    "worker_pid": 5555,
                    "started": now - 300,
                },
                {
                    "id": "r-old",
                    "status": "error",
                    "started": now - 40 * 3600,
                    "ended": now - 30 * 3600,
                },
            ]
            for r in recs:
                with open(os.path.join(m.RUNS_DIR, r["id"] + ".json"), "w") as f:
                    json.dump(r, f)
            (dispatched, killed) = ([], [])
            m.dispatch_detached = lambda action, rid: dispatched.append((rid, action))
            m.kill_worker_inline = lambda rec: killed.append(rec["id"])
            m.pid_alive = lambda pid: False
            m.worker_alive = lambda rec: rec.get("id") == "r-orph"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                m.cmd_reconcile(["--heal"])
            check(
                set(dispatched)
                == {("r-err", "retry"), ("r-int", "resume"), ("r-orph", "resume")},
                "heal dispatch: error->retry, interrupted->resume, orphaned->resume",
            )
            check(
                killed == ["r-orph"], "orphaned worker killed inline before its resume"
            )
            check(
                all((rid != "r-old" for (rid, _) in dispatched)),
                "run that ENDED 30h ago aged out (skipped)",
            )
            check(
                ("r-err", "retry") in dispatched,
                "old-START recent-END run healed (age cutoff uses ended, not started)",
            )
            sh = m.load_self_heal()
            check(
                set(sh) == {"r-err", "r-int", "r-orph"}
                and sh["r-err"]["attempts"] == 1,
                "each heal recorded in the self-heal ledger",
            )
            dispatched.clear()
            killed.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                m.cmd_reconcile(["--heal"])
            check(
                dispatched == [] and killed == [],
                "second --heal pass re-dispatches NOTHING (dedup via self-heal record)",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                m.cmd_reconcile(["--heal", "--allow-repeat"])
            check(len(dispatched) == 3, "--allow-repeat deliberately heals again")
    finally:
        (
            m.STATE_DIR,
            m.RUNS_DIR,
            m.LOGS_DIR,
            m.EVENTS_DIR,
            m.SELF_HEAL_FILE,
            m.HISTORY_PENDING,
            m.dispatch_detached,
            m.kill_worker_inline,
            m.worker_alive,
            m.pid_alive,
        ) = saved


def test_agent_queue():
    """Global agent-budget governor: FIFO-by-batch admission, MONOTONIC partial grants (a head task
    waiting on N agents gets freed slots before any later task starts), a concurrent-task cap (gated
    on BOTH agents and tasks), and crash-reaping of a dead session's reservation."""
    print("test_agent_queue")
    saved = {k: getattr(m, k) for k in ("STATE_DIR", "AGENT_QUEUE_FILE")}
    saved_sess = os.environ.get("NEOMAX_ORCH_SESSION")
    saved_dead = m._session_known_dead
    with tempfile.TemporaryDirectory() as st:
        m.STATE_DIR = st
        m.AGENT_QUEUE_FILE = os.path.join(st, "agent-queue.json")
        os.environ["NEOMAX_ORCH_SESSION"] = "sessX"
        try:
            m.queue_set(agent_budget=50, task_budget=0)
            m.queue_reserve("X", 40)
            a = m.queue_reserve("A", 15, batch="find-1")
            b = m.queue_reserve("B", 5, batch="find-1")
            check(
                a["granted"] == 10 and a["want"] - a["granted"] == 5,
                "FIFO: A gets 10 of 15 (only 10 free) and waits on 5 — the exact scenario",
            )
            check(
                b["granted"] == 0,
                "B (behind A) gets 0 — the front of the line is filled first",
            )
            m.queue_release(task="X")
            (ap, bp) = (m.queue_poll(task="A"), m.queue_poll(task="B"))
            check(
                ap["granted"] == 15,
                "after X frees slots, A fills to its FULL 15 first (monotonic, FIFO)",
            )
            check(bp["granted"] == 5, "THEN B starts and gets its 5")
            check(
                ap.get("batch") == "find-1" and bp.get("batch") == "find-1",
                "batch tag grouped (FIFO-by-batch)",
            )
            m.queue_reserve("C", 100)
            check(
                m.queue_poll(task="A")["granted"] == 15
                and m.queue_poll(task="B")["granted"] == 5,
                "existing grants are NEVER revoked when a new task queues (monotonic)",
            )
            check(
                m.queue_poll(task="C")["granted"] == 30,
                "new task C gets only the remaining free slots (50-20)",
            )
            for t in ("A", "B", "C"):
                m.queue_release(task=t)
            m.queue_set(task_budget=1)
            p = m.queue_reserve("P", 5)
            q = m.queue_reserve("Q", 5)
            check(
                p["granted"] == 5 and q["granted"] == 0,
                "task cap=1: Q waits even though 45 agents are free (admission gated on agents AND tasks)",
            )
            m.queue_release(task="P")
            check(
                m.queue_poll(task="Q")["granted"] == 5,
                "Q starts once the task slot frees",
            )
            m.queue_release(task="Q")
            m.queue_set(task_budget=0)
            m.queue_reserve("Z", 10, session="deadsess")
            m._session_known_dead = lambda s: s == "deadsess"
            snap = m.queue_snapshot()
            check(
                all((r["task"] != "Z" for r in snap["queue"])),
                "a crashed (known-dead) session's reservation is reaped, freeing its slots",
            )
            m._session_known_dead = lambda s: False
            m.queue_set(agent_budget=10, task_budget=0)
            m.queue_reserve("D", 10, session="deadsess2")
            w = m.queue_reserve("W", 5)
            check(w["granted"] == 0, "W waits behind D, which holds all 10 slots")
            m._session_known_dead = lambda s: s == "deadsess2"
            snap2 = m.queue_snapshot()
            wq = [r for r in snap2["queue"] if r["task"] == "W"]
            check(
                all((r["task"] != "D" for r in snap2["queue"]))
                and wq
                and (wq[0]["granted"] == 5),
                "queue_snapshot reaps D and shows W GRANTED its 5 in the SAME snapshot (post-allocate, not stale)",
            )
            m._session_known_dead = lambda s: False
            m.queue_release(task="W")
            m.queue_set(agent_budget=50, task_budget=0)
            try:
                m.cmd_queue(["set-budget", "--agents", "notanumber"])
                check(
                    False, "queue set-budget with a non-integer should exit, not return"
                )
            except SystemExit as ex:
                check(
                    ex.code == 2,
                    "queue set-budget rejects a non-integer --agents with exit(2), no traceback",
                )
        finally:
            m._session_known_dead = saved_dead
            for k, v in saved.items():
                setattr(m, k, v)
            if saved_sess is None:
                os.environ.pop("NEOMAX_ORCH_SESSION", None)
            else:
                os.environ["NEOMAX_ORCH_SESSION"] = saved_sess


def test_tidy_merged_runs():
    """Merged-run auto-resolution: a run whose branch is already merged into main is detected as
    resolved (the check `clean`'s base-ahead count can't do), `tidy` resolves it + advances a
    linked task to 'merged', and a run with commits NOT in main is left alone (fail-safe)."""
    print("test_tidy_merged_runs")
    import subprocess as sp

    saved = {
        k: getattr(m, k)
        for k in (
            "STATE_DIR",
            "RUNS_DIR",
            "TASKS_FILE",
            "archive_run",
            "remove_logs",
            "run_path",
            "owned_by_other_live_orch",
        )
    }
    with tempfile.TemporaryDirectory() as st:
        repo = os.path.join(st, "repo")
        os.makedirs(repo)

        def g(*a):
            return sp.run(["git", *a], cwd=repo, capture_output=True, text=True)

        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(repo, "f"), "w").write("1")
        g("add", "-A")
        g("commit", "-qm", "base")
        base = g("rev-parse", "HEAD").stdout.strip()
        g("checkout", "-q", "-b", "neomax/merged-1")
        open(os.path.join(repo, "f"), "w").write("2")
        g("add", "-A")
        g("commit", "-qm", "work")
        g("checkout", "-q", "main")
        g("merge", "-q", "--no-ff", "neomax/merged-1", "-m", "merge")
        g("checkout", "-q", "-b", "neomax/open-1", base)
        open(os.path.join(repo, "f"), "w").write("3")
        g("add", "-A")
        g("commit", "-qm", "wip")
        g("checkout", "-q", "main")
        m.STATE_DIR = st
        m.RUNS_DIR = os.path.join(st, "runs")
        os.makedirs(m.RUNS_DIR)
        m.TASKS_FILE = os.path.join(st, "tasks.json")
        m.archive_run = lambda rec: None
        m.remove_logs = lambda rec: None
        m.run_path = lambda rid: os.path.join(m.RUNS_DIR, rid + ".json")
        m.owned_by_other_live_orch = lambda rec: False
        try:
            merged = {
                "id": "merged-1",
                "repo": repo,
                "branch": "neomax/merged-1",
                "base": base,
                "status": "done",
                "acknowledged": False,
                "project": "alpha",
            }
            openr = {
                "id": "open-1",
                "repo": repo,
                "branch": "neomax/open-1",
                "base": base,
                "status": "done",
                "acknowledged": False,
                "project": "alpha",
            }
            check(
                m.run_merged_into_main(merged),
                "a branch merged into main is detected as merged",
            )
            check(
                not m.run_merged_into_main(openr),
                "a branch with commits NOT in main is NOT merged (kept)",
            )
            check(
                isinstance(
                    m.run_merged_into_main({"repo": repo, "branch": "nope"}), bool
                ),
                "an unknown/missing branch returns a bool, never crashes",
            )
            for r in (merged, openr):
                with open(m.run_path(r["id"]), "w") as f:
                    json.dump(r, f)
            tid = m.task_add("ship the merged work", project="alpha")
            m.task_update(tid, run="merged-1")
            picked = [r["id"] for r in m.merged_terminal_runs()]
            check(
                picked == ["merged-1"], "merged_terminal_runs picks ONLY the merged run"
            )
            import io, contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                m.cmd_tidy([])
            check(
                "resolved 1 merged run" in buf.getvalue(),
                "tidy resolves the merged run",
            )
            check(
                not os.path.exists(m.run_path("merged-1"))
                and os.path.exists(m.run_path("open-1")),
                "tidy removed the merged run record, left the unmerged one",
            )
            check(
                m.load_tasks()["tasks"][tid]["status"] == "merged",
                "the task linked to the merged run is auto-advanced to 'merged'",
            )
        finally:
            for k, v in saved.items():
                setattr(m, k, v)
