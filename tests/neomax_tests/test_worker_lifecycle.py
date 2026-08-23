"""Worker validation, cleanup, process liveness, failover, and detached dispatch."""

from .support import *


def test_git_missing_cwd():
    print("test_git_missing_cwd")
    r = m.git(["status"], "/nonexistent/dir/here")
    check(
        r.returncode != 0 and r.stdout == "",
        "git() on missing cwd returns failed result, no crash",
    )
    check(
        m.worktree_changes(
            {"repo": "/gone", "worktree": "/gone", "branch": "b", "base": "a"}
        )
        == (False, 0),
        "worktree_changes on gone repo → (False, 0)",
    )


def test_clean_force_repo_deleted():
    print("test_clean_force_repo_deleted")
    with tempfile.TemporaryDirectory() as state:
        runs = os.path.join(state, "runs")
        os.makedirs(runs)
        wt = os.path.join(state, "worktrees", "wt-x")
        os.makedirs(wt)
        gone_repo = os.path.join(state, "deleted-repo")
        rid = "20990101-000000-1"
        rec = {
            "id": rid,
            "engine": "codex",
            "status": "done",
            "acknowledged": True,
            "repo": gone_repo,
            "worktree": wt,
            "branch": "neomax/x",
            "base": "HEAD",
            "pid": 1,
            "started": 0,
        }
        with open(os.path.join(runs, rid + ".json"), "w") as f:
            json.dump(rec, f)
        env = _env(state, state + "/p1", state + "/p2")
        r = subprocess.run(
            [sys.executable, NEOMAX, "clean", "--force", rid],
            env=env,
            capture_output=True,
            text=True,
        )
        check(r.returncode == 0, "clean --force exits 0 when parent repo is gone")
        check("Traceback" not in r.stderr, "clean --force does not crash")
        check(not os.path.isdir(wt), "dangling worktree dir removed (no leak)")
        check(
            not os.path.exists(os.path.join(runs, rid + ".json")), "run record removed"
        )


def test_kill_pause_lossless():
    """`neomax kill` = lossless PAUSE. With a LIVE supervisor, kill signals the
    SUPERVISOR (whose on_signal aborts + keeps the worktree) — never just the worker
    (that raced the supervisor into classifying 'error' and cleaning the worktree).
    Orphan fallback killpgs the worker itself. worktree_outcome honors the sticky
    'killed' flag (in-memory or on-disk) so a killed run's worktree/branch survive."""
    print("test_kill_pause_lossless")
    import time as _t

    saved = (
        m.RUNS_DIR,
        m.load_run,
        m.save_run,
        m.pid_alive,
        m.worker_alive,
        m.worktree_outcome,
        m.log_event,
        getattr(m.os, "kill"),
        getattr(m.os, "killpg"),
        getattr(m.os, "getpgid"),
        m.time.sleep,
    )
    try:
        with tempfile.TemporaryDirectory() as st:
            calls = {"oskill": [], "killpg": []}
            rec = {"id": "r1", "status": "running", "pid": 4242, "worker_pid": 5555}
            store = {"r1": dict(rec)}
            m.load_run = lambda rid: dict(store.get(rid)) if store.get(rid) else None
            m.save_run = lambda r: store.__setitem__(r["id"], dict(r))
            m.pid_alive = lambda pid: pid == 4242
            m.worker_alive = lambda r: False
            polls = {"n": 0}

            def fake_kill(pid, sig):
                calls["oskill"].append((pid, sig))
                store["r1"].update(status="aborted", worktree_state="empty_kept")

            m.os.kill = fake_kill
            m.os.killpg = lambda pg, sig: calls["killpg"].append((pg, sig))
            m.os.getpgid = lambda pid: pid
            m.time.sleep = lambda s: None
            m.worktree_outcome = lambda r: None
            m.log_event = lambda *a, **k: None
            m.cmd_kill(["r1"])
            check(
                calls["oskill"] and calls["oskill"][0][0] == 4242,
                "live supervisor: kill signals the SUPERVISOR pid",
            )
            check(
                not calls["killpg"],
                "live supervisor: worker group is NOT killpg'd directly",
            )
            check(
                store["r1"]["status"] == "aborted" and store["r1"].get("killed"),
                "run aborted via supervisor + sticky killed flag persisted",
            )
            calls["oskill"].clear()
            calls["killpg"].clear()
            store["r2"] = {
                "id": "r2",
                "status": "running",
                "pid": 4242,
                "worker_pid": 6666,
            }
            m.pid_alive = lambda pid: False
            m.worker_alive = lambda r: r.get("worker_pid") == 6666
            wt_calls = []
            m.worktree_outcome = lambda r: wt_calls.append(r.get("killed"))
            m.cmd_kill(["r2"])
            check(
                calls["killpg"] and calls["killpg"][0][0] == 6666,
                "orphan: worker process group killed directly",
            )
            check(
                store["r2"]["status"] == "aborted" and store["r2"].get("killed"),
                "orphan: run marked aborted with sticky killed flag",
            )
            check(
                wt_calls == [True],
                "orphan: worktree_outcome sees killed=True (never cleans)",
            )
    finally:
        (
            m.RUNS_DIR,
            m.load_run,
            m.save_run,
            m.pid_alive,
            m.worker_alive,
            m.worktree_outcome,
            m.log_event,
            m.os.kill,
            m.os.killpg,
            m.os.getpgid,
            m.time.sleep,
        ) = saved


def test_worker_alive_group_and_orphan_sweep():
    """worker_alive checks the worker's PROCESS GROUP, not just the leader pid — so a Codex
    worker whose `codex exec` node wrapper exited but whose native child keeps running still
    reads as alive (was vanishing from the dashboard). And killpg on the group sweeps orphaned
    descendants (vitest/jest pools) after the leader exits — the orphan-CPU fix's mechanism."""
    print("test_worker_alive_group_and_orphan_sweep")
    import subprocess as _sp, time as _t, signal as _sig

    saved = (m.pid_alive, m.subprocess.run, m._group_has_engine)
    try:
        m.pid_alive = lambda p: True
        m.subprocess.run = lambda *a, **k: type(
            "R", (), {"stdout": "node /x/codex exec --json", "returncode": 0}
        )()
        check(
            m.worker_alive({"worker_pid": 111, "engine": "codex"}),
            "leader alive + codex command -> worker_alive True",
        )
        m.pid_alive = lambda p: False
        m._group_has_engine = lambda pgid, eng: True
        check(
            m.worker_alive({"worker_pid": 222, "engine": "codex"}),
            "leader dead + group has codex -> worker_alive True (node-wrapper-exited case)",
        )
        m._group_has_engine = lambda pgid, eng: False
        check(
            not m.worker_alive({"worker_pid": 333, "engine": "codex"}),
            "leader dead + empty group -> worker_alive False",
        )
        check(not m.worker_alive({"engine": "codex"}), "no worker_pid -> False")
    finally:
        (m.pid_alive, m.subprocess.run, m._group_has_engine) = saved
    sample = "  4242 /usr/local/bin/node /x/codex exec --json\n  9999 /bin/zsh\n"
    sv = m.subprocess.run
    try:
        m.subprocess.run = lambda *a, **k: type(
            "R", (), {"stdout": sample, "returncode": 0}
        )()
        check(
            m._group_has_engine(4242, "codex"),
            "_group_has_engine: finds codex in pgid 4242",
        )
        check(
            not m._group_has_engine(9999, "codex"),
            "_group_has_engine: pgid 9999 (zsh) is not the engine",
        )
        check(
            not m._group_has_engine(5555, "codex"),
            "_group_has_engine: absent pgid -> False",
        )
    finally:
        m.subprocess.run = sv
    leader = _sp.Popen(["/bin/sh", "-c", "sleep 30 & sleep 30"], start_new_session=True)
    _t.sleep(0.4)
    try:
        alive_before = m._group_has_engine
        try:
            os.killpg(leader.pid, 0)
            group_existed = True
        except OSError:
            group_existed = False
        check(group_existed, "spawned worker group exists before sweep")
        try:
            os.killpg(leader.pid, _sig.SIGKILL)
        except OSError:
            pass
        try:
            leader.wait(timeout=2)
        except Exception:
            pass
        deadline = _t.time() + 2
        still = True
        while still and _t.time() < deadline:
            rows = _sp.run(
                ["ps", "-axo", "pgid=,stat="], capture_output=True, text=True
            ).stdout.splitlines()
            still = any(
                (
                    parts[0] == str(leader.pid) and (not parts[1].startswith("Z"))
                    for parts in (row.strip().split(None, 1) for row in rows)
                    if len(parts) == 2
                )
            )
            if still:
                _t.sleep(0.05)
        check(
            not still,
            "killpg swept the whole worker group — no runnable descendants survive",
        )
    finally:
        try:
            os.killpg(leader.pid, _sig.SIGKILL)
        except OSError:
            pass
        try:
            leader.wait(timeout=2)
        except Exception:
            pass


def test_worktree_outcome_killed_guard():
    """A clean (no-changes) worktree of an explicitly-KILLED run is KEPT even if the
    status says 'error' (the race outcome) — in-memory flag or the on-disk record."""
    print("test_worktree_outcome_killed_guard")
    saved = (m.worktree_changes, m.git, m.RUNS_DIR)
    try:
        with tempfile.TemporaryDirectory() as st:
            wt = os.path.join(st, "wt")
            os.makedirs(wt)
            m.RUNS_DIR = st
            m.worktree_changes = lambda r: (False, 0)
            git_calls = []
            m.git = lambda args, repo=None: (
                git_calls.append(args)
                or type("R", (), {"returncode": 0, "stdout": ""})()
            )
            rec = {
                "id": "rk",
                "status": "error",
                "repo": st,
                "worktree": wt,
                "branch": "b",
                "base": "HEAD",
            }
            with open(os.path.join(st, "rk.json"), "w") as f:
                json.dump({"id": "rk", "killed": True}, f)
            m.worktree_outcome(rec)
            check(
                not any(
                    (
                        "remove" in a
                        for a in git_calls
                        for a in [a[1] if len(a) > 1 else ""]
                    )
                ),
                "killed-on-disk: worktree NOT removed despite clean+error",
            )
            check(
                rec["worktree_state"] == "empty_kept",
                "killed run's worktree kept (resume-ready)",
            )
            git_calls.clear()
            rec2 = {
                "id": "rn",
                "status": "error",
                "repo": st,
                "worktree": wt,
                "branch": "b",
                "base": "HEAD",
            }
            m.worktree_outcome(rec2)
            check(
                rec2["worktree_state"] == "cleaned",
                "un-killed clean error run still cleans up",
            )
    finally:
        (m.worktree_changes, m.git, m.RUNS_DIR) = saved


def test_cross_engine_failover():
    """When a worker hits a usage LIMIT and its whole engine pool is exhausted, the TASK
    continues on the other engine (same worktree/branch): engine flips, model cleared
    (never carry a model id across engines), prompt carries the cross-engine continue
    note. Errors (non-limit) do NOT cross engines."""
    print("test_cross_engine_failover")
    saved = (
        m.execute,
        m.pick_account,
        m.maybe_cooldown,
        m.save_run,
        m.log_event,
        m.remember_session,
        m.finish,
        m.worker_profiles,
        m.allowed_engines,
        m.engine_in_scope,
    )
    try:
        calls = {"execute": 0}

        def fake_execute(rec):
            calls["execute"] += 1
            return "limit" if calls["execute"] == 1 else "done"

        m.execute = fake_execute

        def fake_pick(sel, exclude=(), engine="claude", bias=None):
            if engine == "claude":
                raise SystemExit(1)
            return "/h/.opencode" if engine == "opencode" else "/h/.codex"

        m.pick_account = fake_pick
        m.maybe_cooldown = lambda rec, st: None
        m.save_run = lambda rec: None
        m.log_event = lambda *a, **k: None
        m.remember_session = lambda rec: None
        m.worker_profiles = lambda e: ["/h/a", "/h/b"]
        m.allowed_engines = lambda: {"claude", "codex", "opencode"}
        m.engine_in_scope = lambda e: True
        done = {}

        def fake_finish(rec, status):
            done.update(rec, _status=status)
            raise SystemExit(0)

        m.finish = fake_finish
        rec = {
            "id": "x",
            "engine": "claude",
            "profile": "/h/a",
            "attempt": 1,
            "prompt": "task",
            "_prompt_to_send": "task",
            "model": "claude-opus-4-8",
            "effort": "max",
            "session": "s1",
        }
        try:
            m.run_with_failover(rec)
        except SystemExit as e:
            check(e.code == 0, "run finished cleanly after cross-engine continue")
        check(
            done.get("engine") == "opencode" and done.get("profile") == "/h/.opencode",
            "limit + exhausted Claude pool -> task prefers free OpenCode",
        )
        check(
            done.get("model") == m.OPENCODE_MODEL,
            "cross-engine target is re-pinned to exact OX Alpha",
        )
        check(
            done.get("effort") is None,
            "OpenCode cross-engine target drops foreign effort settings",
        )
        check(
            "DIFFERENT agent" in done.get("_prompt_to_send", ""),
            "cross-engine continuation note injected",
        )
        check(done.get("_status") == "done", "second attempt completed the task")
        calls["execute"] = 0
        m.execute = lambda rec: "error"
        done.clear()
        rec2 = {
            "id": "y",
            "engine": "claude",
            "profile": "/h/a",
            "attempt": 1,
            "prompt": "t",
            "_prompt_to_send": "t",
            "session": "s2",
        }
        try:
            m.run_with_failover(rec2)
        except SystemExit:
            pass
        check(
            done.get("engine") == "claude" and done.get("_status") == "error",
            "non-limit error with exhausted pool finishes (no engine cross)",
        )
    finally:
        (
            m.execute,
            m.pick_account,
            m.maybe_cooldown,
            m.save_run,
            m.log_event,
            m.remember_session,
            m.finish,
            m.worker_profiles,
            m.allowed_engines,
            m.engine_in_scope,
        ) = saved


def test_dispatch_detach_default():
    """`neomax delegate auto` DETACHES its supervisor into its own session by default, so the worker
    SURVIVES the launching shell — the regression for: an orchestrator launches `auto` from a Bash
    call, that shell is reaped at turn-end (or hits the tool timeout ~4-5 min), the inline supervisor
    takes SIGHUP, and its handler kills the worker (all 8 'vanished at once'). EXCEPTION: under the
    run-all scheduler (--run-id) it stays INLINE — run-all already spawns each part detached and
    registry-tracks it, so a second detach layer is redundant. --wait forces inline; --detach forces."""
    print("test_dispatch_detach_default")
    keys = (
        "pick_account",
        "engine_profiles",
        "save_run",
        "log_event",
        "current_orch_session",
        "run_with_failover",
        "detach_supervisor",
        "delegation_brief_check",
        "allowed_engines",
        "make_worktree",
        "subprocess",
        "LOGS_DIR",
    )
    saved = {k: getattr(m, k) for k in keys}
    try:
        called = {"path": None}
        m.pick_account = lambda sel, engine="claude", **k: "/h/.codex-acct2"
        m.engine_profiles = lambda e: ["/h/.codex", "/h/.codex-acct2"]
        m.allowed_engines = lambda: set(m.ENGINES)
        m.save_run = lambda rec: None
        m.log_event = lambda *a, **k: None
        m.current_orch_session = lambda: "sess"
        m.delegation_brief_check = lambda *a, **k: None
        m.run_with_failover = lambda rec: called.__setitem__("path", "inline")
        m.detach_supervisor = lambda rec: called.update(path="detach", rec=rec)

        def go(args):
            called["path"] = None
            m.cmd_run(args)
            return called["path"]

        base = ["--engine", "codex", "--no-worktree"]
        prm = "do the actual work to completion here please"
        check(
            go(base + ["auto", prm]) == "detach",
            "plain `auto` detaches by default (worker survives the launching shell)",
        )
        check(
            go(base + ["--wait", "auto", prm]) == "inline",
            "--wait blocks in the foreground (old behavior preserved)",
        )
        check(
            go(base + ["--foreground", "auto", prm]) == "inline",
            "--foreground is an alias for --wait",
        )
        check(
            go(base + ["--run-id", "PLAN-p1", "auto", prm]) == "inline",
            "--run-id (run-all scheduler) stays INLINE — no redundant second detach",
        )
        check(
            go(base + ["--detach", "--run-id", "PLAN-p1", "auto", prm]) == "detach",
            "explicit --detach overrides the scheduler-inline default",
        )
        for engine, model in (
            ("claude", "custom-claude"),
            ("codex", "custom-codex"),
            ("opencode", "opencode/big-pickle"),
            ("kimi", "custom-kimi"),
            ("grok", "custom-grok"),
        ):
            go(["--engine", engine, "--model", model, "--no-worktree", "auto", prm])
            check(
                called["rec"]["model"] == model,
                "generic --model is recorded for the %s worker" % engine,
            )
        go(["--engine", "claude", "--opus", "--no-worktree", "auto", prm])
        check(
            called["rec"]["model"] == "claude-opus-5[1m]",
            "--opus explicitly opts a Claude worker into Opus 5",
        )
        with tempfile.TemporaryDirectory() as ld:
            m.LOGS_DIR = ld
            m.save_run = lambda rec: None
            m.detach_supervisor = saved["detach_supervisor"]
            spawn = {"argv": None, "kw": None}

            class _FakePopen:
                def __init__(s, argv, **kw):
                    (spawn["argv"], spawn["kw"]) = (argv, kw)

            class _ShimSub:
                Popen = _FakePopen
                DEVNULL = m.subprocess.DEVNULL

            m.subprocess = _ShimSub
            m.detach_supervisor({"id": "20260617-000000-1"})
            check(
                spawn["argv"][1] == "__supervise"
                and spawn["argv"][2] == "20260617-000000-1"
                and (spawn["kw"].get("start_new_session") is True),
                "detach_supervisor spawns `neomax __supervise <id>` in a NEW session (immune to launcher SIGHUP)",
            )
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
