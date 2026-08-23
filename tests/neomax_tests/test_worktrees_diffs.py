"""Worktree recovery, cleanup safety, planning scouts, subagent history, and exact diffs."""

from .support import *


def test_worktree_changes_fail_safe():
    """worktree_changes FAILS SAFE: a git query that errors (unreachable base SHA,
    bogus branch) returns (True, 1) = 'assume work present' — never (False, 0) from
    trusting empty stdout. THE silent-data-loss fix: rev-list on a gc'd/rebased base
    exits 128 with empty stdout, which the old int(''or'0') read as 0-ahead and
    `clean` then branch -D'd real committed work."""
    print("test_worktree_changes_fail_safe")
    with tempfile.TemporaryDirectory() as tmp:
        (repo, wt, g) = _mk_repo(tmp)
        rec = {"repo": repo, "worktree": wt, "branch": "neomax/t1", "base": "main"}
        check(
            m.worktree_changes(rec) == (False, 1),
            "clean worktree 1 commit ahead -> (False, 1)",
        )
        with open(os.path.join(wt, "scratch.txt"), "w") as f:
            f.write("uncommitted\n")
        check(
            m.worktree_changes(rec) == (True, 1),
            "uncommitted file -> dirty=True (still 1 ahead)",
        )
        os.remove(os.path.join(wt, "scratch.txt"))
        bad_base = dict(rec, base="0" * 40)
        check(
            m.worktree_changes(bad_base) == (True, 1),
            "unreachable base SHA -> (True, 1) assume-work-present, NOT (False, 0)",
        )
        check(
            m.worktree_changes(dict(rec, branch="no/such/branch")) == (True, 1),
            "bogus branch -> (True, 1) fail-safe",
        )
        check(
            m.worktree_changes(dict(rec, worktree=os.path.join(tmp, "gone")))
            == (False, 0),
            "missing worktree dir -> (False, 0)",
        )
        check(
            m.worktree_changes(dict(rec, repo=os.path.join(tmp, "norepo")))
            == (False, 0),
            "missing parent repo -> (False, 0)",
        )


def test_clean_refuses_unmerged_work():
    """`clean` (no --force) must SKIP a run whose branch has commits ahead — and also
    when the work is merely UNVERIFIABLE (bogus base -> fail-safe). --force discards."""
    print("test_clean_refuses_unmerged_work")
    with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as tmp:
        (repo, wt, g) = _mk_repo(tmp, branch="neomax/t2")
        runs = os.path.join(state, "runs")
        os.makedirs(runs)
        rid = "20990101-000000-7"
        rec = {
            "id": rid,
            "engine": "claude",
            "status": "done",
            "acknowledged": True,
            "repo": repo,
            "worktree": wt,
            "branch": "neomax/t2",
            "base": "main",
            "pid": 1,
            "started": 0,
        }
        rec_path = os.path.join(runs, rid + ".json")
        with open(rec_path, "w") as f:
            json.dump(rec, f)
        env = _env(state, state + "/p1", state + "/p2")
        r = subprocess.run(
            [sys.executable, NEOMAX, "clean", rid],
            env=env,
            capture_output=True,
            text=True,
        )
        check(
            r.returncode == 0 and "unmerged work" in r.stderr,
            "clean (no --force) SKIPs a run with commits ahead",
        )
        check(
            g("rev-parse", "--verify", "neomax/t2").returncode == 0,
            "branch with unmerged work survives clean",
        )
        check(
            os.path.isdir(wt) and os.path.exists(rec_path),
            "worktree + run record kept (nothing deleted)",
        )
        rec["base"] = "0" * 40
        with open(rec_path, "w") as f:
            json.dump(rec, f)
        r2 = subprocess.run(
            [sys.executable, NEOMAX, "clean", rid],
            env=env,
            capture_output=True,
            text=True,
        )
        check(
            r2.returncode == 0
            and "unmerged work" in r2.stderr
            and (g("rev-parse", "--verify", "neomax/t2").returncode == 0),
            "unverifiable base -> clean refuses (fail-safe protects the branch)",
        )
        rec["base"] = "main"
        with open(rec_path, "w") as f:
            json.dump(rec, f)
        r3 = subprocess.run(
            [sys.executable, NEOMAX, "clean", "--force", rid],
            env=env,
            capture_output=True,
            text=True,
        )
        check(
            r3.returncode == 0 and "Traceback" not in r3.stderr, "clean --force exits 0"
        )
        check(
            g("rev-parse", "--verify", "neomax/t2").returncode != 0,
            "clean --force deletes the branch (that's what --force is for)",
        )
        check(
            not os.path.isdir(wt) and (not os.path.exists(rec_path)),
            "clean --force removes worktree + run record",
        )


def test_plan_mode_scouts():
    """--plan = READ-ONLY scout: claude gets --permission-mode plan (NOT
    --dangerously-skip-permissions); codex gets -s read-only (NOT the bypass flag).
    Normal workers unchanged."""
    print("test_plan_mode_scouts")
    ca = m.claude_args(
        {
            "engine": "claude",
            "_prompt_to_send": "scout",
            "session": "s",
            "plan_mode": True,
        }
    )
    check(
        "--permission-mode" in ca and ca[ca.index("--permission-mode") + 1] == "plan",
        "plan-mode claude scout runs --permission-mode plan",
    )
    check(
        "--dangerously-skip-permissions" not in ca,
        "plan scout does NOT skip permissions",
    )
    ca2 = m.claude_args({"engine": "claude", "_prompt_to_send": "work", "session": "s"})
    check(
        "--dangerously-skip-permissions" in ca2 and "--permission-mode" not in ca2,
        "normal claude worker unchanged (full permissions)",
    )
    xa = m.codex_args(
        {
            "engine": "codex",
            "_prompt_to_send": "scout",
            "workdir": "/tmp",
            "model": "gpt-5.5",
            "effort": "high",
            "plan_mode": True,
        }
    )
    check(
        "-s" in xa and xa[xa.index("-s") + 1] == "read-only",
        "plan-mode codex scout is read-only sandbox",
    )
    check(
        "--dangerously-bypass-approvals-and-sandbox" not in xa,
        "plan codex scout does NOT bypass sandbox",
    )
    xa2 = m.codex_args(
        {
            "engine": "codex",
            "_prompt_to_send": "work",
            "workdir": "/tmp",
            "model": "gpt-5.5",
            "effort": "high",
        }
    )
    check(
        "--dangerously-bypass-approvals-and-sandbox" in xa2,
        "normal codex worker unchanged",
    )


def test_subagent_history_stats():
    """Sub-agent history: file/line stats extracted from Edit/Write/MultiEdit tool_use
    blocks in the persisted transcripts; worker sub-agents excluded; status from the
    final turn."""
    print("test_subagent_history_stats")
    import time as _t

    saved = os.environ.get("NEOMAX_PROFILES")
    try:
        with tempfile.TemporaryDirectory() as st:
            prof = os.path.join(st, ".claude")
            sub = os.path.join(prof, "projects", "-p", "sess-1", "subagents")
            os.makedirs(sub)
            os.environ["NEOMAX_PROFILES"] = prof
            f = os.path.join(sub, "agent-x1.jsonl")
            with open(f, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": "/workspace/repo",
                            "timestamp": "2026-06-11T10:00:00Z",
                            "message": {"role": "user", "content": "Fix the gateway"},
                        }
                    )
                    + "\n"
                )
                fh.write(
                    json.dumps(
                        {
                            "type": "user",
                            "toolUseResult": {
                                "filePath": "/r/a.ts",
                                "structuredPatch": [
                                    {
                                        "oldStart": 1,
                                        "oldLines": 2,
                                        "newStart": 1,
                                        "newLines": 4,
                                        "lines": [" x", " y", "+z", "+w"],
                                    }
                                ],
                            },
                        }
                    )
                    + "\n"
                )
                fh.write(
                    json.dumps(
                        {
                            "type": "user",
                            "toolUseResult": {
                                "type": "create",
                                "filePath": "/r/b.ts",
                                "content": "1\n2\n3",
                            },
                        }
                    )
                    + "\n"
                )
                fh.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "done"}],
                            },
                        }
                    )
                    + "\n"
                )
            old = _t.time() - 100
            os.utime(f, (old, old))
            rows = m.gather_subagent_history(days=1)
            check(len(rows) == 1, "one sub-agent recovered from its transcript")
            r = rows[0]
            check(
                r["status"] == "done" and r["label"] == "Fix the gateway",
                "status + mission label extracted",
            )
            by = {x["path"]: x for x in r["files"]}
            check(
                by["/r/a.ts"]["adds"] == 2 and by["/r/a.ts"]["dels"] == 0,
                "EXACT structuredPatch stats: +2 added lines (z,w), 0 deleted",
            )
            check(
                by["/r/b.ts"]["adds"] == 3 and by["/r/b.ts"]["dels"] == 0,
                "Write create: +3 lines",
            )
            ex = m.extract_claude_exact_patches(f)
            check(
                ex["n_edits"] == 2
                and "@@ -1,2 +1,4 @@" in ex["files"]["/r/a.ts"]["hunks"][0],
                "extract_claude_exact_patches returns the verbatim unified hunk",
            )
            f2 = os.path.join(sub, "agent-x2.jsonl")
            with open(f2, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": os.path.join(m.STATE_DIR, "worktrees", "w"),
                            "message": {"role": "user", "content": "x"},
                        }
                    )
                    + "\n"
                )
            os.utime(f2, (old, old))
            check(
                len(m.gather_subagent_history(days=1)) == 1,
                "neomax-worker sub-agents excluded",
            )
    finally:
        if saved is None:
            os.environ.pop("NEOMAX_PROFILES", None)
        else:
            os.environ["NEOMAX_PROFILES"] = saved


def test_exact_diffs_and_projects():
    """Exact-diff extraction from tool_use INPUTS (sub-agent transcripts with no result
    patch) + project tagging by path root."""
    print("test_exact_diffs_and_projects")
    saved = (m.PROJECTS_FILE, m.STATE_DIR)
    try:
        with tempfile.TemporaryDirectory() as st:
            m.STATE_DIR = st
            m.PROJECTS_FILE = os.path.join(st, "projects.json")
            json.dump(
                {
                    "alpha": {
                        "root": "/work/alpha",
                        "repos": ["a"],
                        "branch_prefix": "ax",
                    },
                    "beta": {
                        "root": "/work/beta",
                        "repos": ["b"],
                        "branch_prefix": "bx",
                    },
                },
                open(m.PROJECTS_FILE, "w"),
            )
            check(
                m.project_of("/work/alpha/a/src/x.ts") == "alpha",
                "path under a root -> that project",
            )
            check(m.project_of("/work/beta/b") == "beta", "exact root match")
            check(m.project_of("/elsewhere/x") is None, "unregistered path -> None")
            defs = m._DEFAULT_PROJECTS
            check(
                defs == {}, "tracked runtime ships with no built-in personal projects"
            )
            files = {}
            ln = json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "MultiEdit",
                                "input": {
                                    "file_path": "/r/c.ts",
                                    "edits": [
                                        {"old_string": "a\nb", "new_string": "a\nB\nc"}
                                    ],
                                },
                            }
                        ],
                    },
                }
            )
            n = m._input_exact_diff(files, ln)
            check(n == 1 and "/r/c.ts" in files, "MultiEdit input -> exact diff entry")
            h = files["/r/c.ts"]["hunks"][0]
            check(
                "-b" in h and "+B" in h and ("+c" in h),
                "verbatim old/new lines in the unified hunk",
            )
    finally:
        (m.PROJECTS_FILE, m.STATE_DIR) = saved


def test_extract_patches_bounded_and_robust():
    """Exact-diff extractors are bounded (max_bytes stops huge transcripts) and never
    raise on garbage/missing files — the house rule: a malformed record can't crash a
    ledger-wide command."""
    print("test_extract_patches_bounded_and_robust")
    r = m.extract_claude_exact_patches("/no/such/file.jsonl")
    check(
        r == {"files": {}, "n_edits": 0}, "missing claude transcript -> empty, no raise"
    )
    check(
        m.extract_codex_exact_patches("/no/such.jsonl") == {"files": {}, "n_edits": 0},
        "missing codex rollout -> empty, no raise",
    )
    with tempfile.TemporaryDirectory() as st:
        g = os.path.join(st, "g.jsonl")
        open(g, "w").write("not json\n{bad\n" + "x" * 100 + "\n")
        check(
            m.extract_claude_exact_patches(g)["n_edits"] == 0, "garbage lines skipped"
        )
        big = os.path.join(st, "big.jsonl")
        line = (
            json.dumps(
                {
                    "toolUseResult": {
                        "filePath": "/r/a",
                        "structuredPatch": [
                            {
                                "oldStart": 1,
                                "oldLines": 1,
                                "newStart": 1,
                                "newLines": 2,
                                "lines": [" x", "+y"],
                            }
                        ],
                    }
                }
            )
            + "\n"
        )
        with open(big, "w") as fh:
            for _ in range(5000):
                fh.write(line)
        full = m.extract_claude_exact_patches(big)
        bounded = m.extract_claude_exact_patches(big, max_bytes=len(line) * 10)
        check(
            bounded["n_edits"] < full["n_edits"] and bounded["n_edits"] <= 11,
            "max_bytes bounds how much of a huge transcript is read",
        )


def test_ensure_workdir_recovery():
    """ensure_workdir: a vanished worktree DIRECTORY (TMPDIR purge) is rebuilt under
    WORKTREES_DIR from the run's branch — committed work recovered, NEVER the live
    checkout. Branch-with-committed-work gone -> False sentinel (explicit refusal to
    fall back); branch auto-cleaned (no committed work) -> fresh isolated worktree
    from base; missing parent repo -> False."""
    print("test_ensure_workdir_recovery")
    import io, contextlib, shutil

    saved = (m.STATE_DIR, m.WORKTREES_DIR)
    saved_home = os.environ.get("NEOMAX_HOME")
    try:
        with tempfile.TemporaryDirectory() as st, tempfile.TemporaryDirectory() as tmp:
            os.environ["NEOMAX_HOME"] = st
            m.STATE_DIR = st
            m.WORKTREES_DIR = os.path.join(st, "worktrees")
            (repo, wt, g) = _mk_repo(tmp, branch="neomax/r9")
            rid = "20990101-000000-9"
            rec = {
                "id": rid,
                "repo": repo,
                "branch": "neomax/r9",
                "base": "main",
                "workdir": wt,
                "worktree": wt,
            }
            check(
                m.ensure_workdir(rec) is True and rec["workdir"] == wt,
                "intact workdir -> True, path untouched",
            )
            shutil.rmtree(wt)
            with contextlib.redirect_stderr(io.StringIO()):
                check(
                    m.ensure_workdir(rec) is True,
                    "vanished worktree dir -> recovered (True)",
                )
            new_wt = os.path.join(m.WORKTREES_DIR, "repo-%s" % rid)
            check(
                rec["workdir"] == new_wt
                and rec["worktree"] == new_wt
                and os.path.isdir(new_wt),
                "rebuilt under WORKTREES_DIR (state dir), rec workdir+worktree updated",
            )
            rp = os.path.realpath(rec["workdir"])
            check(
                rp != os.path.realpath(repo)
                and (not rp.startswith(os.path.realpath(repo) + os.sep)),
                "recovered workdir is NOT the live repo checkout",
            )
            check(
                g("rev-parse", "--abbrev-ref", "HEAD", cwd=new_wt).stdout.strip()
                == "neomax/r9",
                "rebuilt worktree is on the run's branch",
            )
            check(
                os.path.exists(os.path.join(new_wt, "new.txt")),
                "committed work recovered from the branch",
            )
            g("worktree", "remove", "--force", new_wt)
            g("branch", "-D", "neomax/r9")
            rec2 = {
                "id": rid,
                "repo": repo,
                "branch": "neomax/r9",
                "base": "main",
                "workdir": new_wt,
                "worktree": new_wt,
                "worktree_state": "has_changes",
            }
            eout = io.StringIO()
            with contextlib.redirect_stderr(eout):
                check(
                    m.ensure_workdir(rec2) is False,
                    "branch-with-committed-work gone -> False (unrecoverable sentinel)",
                )
            check(
                "NOT falling back to the live checkout" in eout.getvalue(),
                "explicitly refuses live-checkout fallback",
            )
            check(
                rec2["workdir"] == new_wt and (not os.path.isdir(new_wt)),
                "rec NOT silently re-pointed (workdir left at the dead path)",
            )
            rec3 = {
                "id": rid,
                "repo": repo,
                "branch": "neomax/r9",
                "base": "main",
                "workdir": new_wt,
                "worktree": new_wt,
                "worktree_state": "cleaned",
            }
            with contextlib.redirect_stderr(io.StringIO()):
                check(
                    m.ensure_workdir(rec3) is True,
                    "auto-cleaned empty run -> fresh isolated worktree (True)",
                )
            check(
                rec3["workdir"] == new_wt
                and os.path.isdir(new_wt)
                and (not os.path.exists(os.path.join(new_wt, "new.txt")))
                and (
                    g("rev-parse", "--abbrev-ref", "HEAD", cwd=new_wt).stdout.strip()
                    == "neomax/r9"
                ),
                "fresh worktree cut from base on the run branch (no phantom prior work)",
            )
            rec4 = {
                "id": rid,
                "repo": os.path.join(tmp, "norepo"),
                "branch": "neomax/r9",
                "base": "main",
                "workdir": os.path.join(tmp, "gone-wd"),
            }
            with contextlib.redirect_stderr(io.StringIO()):
                check(m.ensure_workdir(rec4) is False, "missing parent repo -> False")
    finally:
        (m.STATE_DIR, m.WORKTREES_DIR) = saved
        if saved_home is None:
            os.environ.pop("NEOMAX_HOME", None)
        else:
            os.environ["NEOMAX_HOME"] = saved_home
