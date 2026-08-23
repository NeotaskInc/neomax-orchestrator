"""Status aggregation, ambient sessions, history, subagents, and portal transport."""

from .support import *


def test_history_persistence():
    print("test_history_persistence")
    with tempfile.TemporaryDirectory() as st:
        m.STATE_DIR = st
        m.HISTORY_DB = os.path.join(st, "history.db")
        m.HISTORY_LOGS = os.path.join(st, "history-logs")
        m.LOGS_DIR = os.path.join(st, "logs")
        os.makedirs(m.LOGS_DIR)
        log = os.path.join(m.LOGS_DIR, "20260101-000000-1.attempt1.jsonl")
        open(log, "w").write(
            '{"type":"system","subtype":"task_started","task_id":"k1","description":"child A"}\n'
        )
        rec = {
            "id": "20260101-000000-1",
            "engine": "claude",
            "profile": "/h/.claude-acct2",
            "status": "done",
            "prompt": "do the thing",
            "branch": "neomax/x",
            "ultra": True,
            "children": [{"id": "k1", "label": "child A", "status": "completed"}],
            "started": 100,
            "ended": 200,
            "log": log,
        }
        m.archive_run(rec)
        rows = m.history_runs()
        check(
            len(rows) == 1 and rows[0]["id"] == "20260101-000000-1",
            "run archived to history",
        )
        check(
            rows[0]["children"] == 1 and rows[0]["ultra"] == 1,
            "history keeps sub-agent count + ultra flag",
        )
        got = m.history_get("20260101-000000-1")
        check(
            got and got["prompt"] == "do the thing",
            "full record retrievable from history",
        )
        check(
            got.get("_log_path") and os.path.isfile(got["_log_path"]),
            "worker log archived to history-logs",
        )
        rec["status"] = "integrated"
        m.archive_run(rec)
        rows2 = m.history_runs()
        check(
            len(rows2) == 1 and rows2[0]["status"] == "integrated",
            "re-archive upserts (no dup, status updated)",
        )


def test_live_children_parse():
    print("test_live_children_parse")
    with tempfile.TemporaryDirectory() as st:
        log = os.path.join(st, "run.jsonl")
        open(log, "w").write(
            '{"type":"system","subtype":"task_started","task_id":"a1","description":"build feature","task_type":"local_workflow"}\n{"type":"system","subtype":"task_progress","task_id":"a1","last_tool_name":"Edit","usage":{"total_tokens":4200}}\n'
        )
        kids = m.live_children({"engine": "claude", "log": log})
        check(len(kids) == 1, "live_children parses the running log")
        c = kids[0]
        check(
            c["status"] == "running"
            and c["last_tool"] == "Edit"
            and (c["tokens"] == 4200),
            "live child carries status + last_tool + tokens",
        )


def test_gather_status_subagent_count():
    """gather_status must (1) COUNT a live Claude sub-agent even though its stream sets a
    task_type as the child 'kind' (e.g. 'local_agent') — the headline sub-agent total filters
    by "not a step", NOT "== agent" (the old filter dropped every such child, undercounting),
    and (2) show each running run's LIVE-parsed children in its row, not the STALE stored
    `children` (gather_status must reuse ONE ledger snapshot so the live-parse annotation
    survives into the run-row loop; re-reading all_runs() returned fresh dicts that lost it,
    so rows fell back to leftover codex 'step' records from a prior cross-engine attempt)."""
    print("test_gather_status_subagent_count")
    import copy

    P = "/h/.claude-acct5"
    saved = {
        k: getattr(m, k)
        for k in (
            "all_runs",
            "pid_alive",
            "engine_profiles",
            "logged_in",
            "cooling_down",
            "account_identity",
            "ambient_agent_details",
            "ambient_main_details",
            "live_counts",
            "is_paused",
            "orch_profile",
            "all_orchestrators",
            "load_tasks",
            "_solo_ambient",
            "project_of",
            "profile_acct_no",
        )
    }
    with tempfile.TemporaryDirectory() as st:
        log = os.path.join(st, "run.attempt6.jsonl")
        with open(log, "w") as fh:
            fh.write(
                '{"type":"system","subtype":"task_started","task_id":"k1","description":"read gateway","task_type":"local_agent"}\n'
            )
            fh.write(
                '{"type":"system","subtype":"task_progress","task_id":"k1","last_tool_name":"Read"}\n'
            )
        rec = {
            "id": "20260626-121432-86274",
            "engine": "claude",
            "profile": P,
            "status": "running",
            "pid": 4242,
            "log": log,
            "prompt": "GATEWAY worker",
            "started": 100,
            "acct_no": "5",
            "children": [
                {"id": "item_%d" % i, "kind": "step", "status": "completed"}
                for i in range(1, 4)
            ],
        }
        m.all_runs = lambda: [copy.deepcopy(rec)]
        m.pid_alive = lambda pid: True
        m.engine_profiles = lambda e: [P] if e == "claude" else []
        m.logged_in = lambda p, e: False
        m.cooling_down = lambda p: 0
        m.account_identity = lambda p, e: {}
        m.ambient_agent_details = lambda p, e: []
        m.ambient_main_details = lambda p, e, ww=(): []
        m.live_counts = lambda e: {}
        m.is_paused = lambda p: False
        m.orch_profile = lambda e: None
        m.all_orchestrators = lambda: []
        m.load_tasks = lambda: {"tasks": {}}
        m._solo_ambient = lambda ww=(): ([], 0, 0)
        m.project_of = lambda c: None
        m.profile_acct_no = lambda p, e: "5"
        try:
            s = m.gather_status()
            sm = s["summary"]
            check(
                sm["subagents"] == 1,
                "a live claude sub-agent (kind=task_type, not 'agent') counts toward the headline",
            )
            check(
                sm["agents_total"] == 2,
                "agents_total = 1 worker + 0 mains + 1 sub-agent (sub-agent no longer dropped)",
            )
            row = [r for r in s["runs"] if r["id"] == rec["id"]][0]
            check(
                row["children"] == 1,
                "run row shows the 1 LIVE-parsed child, not the 3 stale stored codex steps",
            )
            check(
                all((c.get("kind") != "step" for c in row["child_list"])),
                "run row's child_list is the live sub-agent, never the leftover codex steps",
            )
        finally:
            for k, v in saved.items():
                setattr(m, k, v)


def test_effective_status_malformed():
    print("test_effective_status_malformed")
    check(
        m.effective_status({"id": "pr-x"}) == "unknown",
        "no-status record -> 'unknown' (no crash)",
    )
    check(
        m.effective_status({"id": "x", "status": "done"}) == "done",
        "normal record unaffected",
    )
    check(
        m.effective_status({"id": "x", "status": None}) == "unknown",
        "None status -> 'unknown'",
    )


def test_ambient_agent_detection():
    """Live sub-agents are detected from their on-disk transcripts for ANY session (not just
    neomax-spawned): active = written within AGENT_ACTIVE_WINDOW_S and last event isn't a
    final end_turn. Finished, stale, and Codex profiles count 0."""
    print("test_ambient_agent_detection")
    import time as _t

    with tempfile.TemporaryDirectory() as st:
        prof = os.path.join(st, ".claude")
        sub = os.path.join(prof, "projects", "-proj", "sess-1", "subagents")
        wf = os.path.join(sub, "workflows", "wf_abc-123")
        os.makedirs(wf)
        now = _t.time()

        def write(path, last_event, age_s, cwd="/workspace/repo"):
            with open(path, "w") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "user",
                            "sessionId": "sess-1",
                            "cwd": cwd,
                            "gitBranch": "main",
                            "slug": "test-slug",
                            "message": {
                                "role": "user",
                                "content": "You are AGENT-X, fix the gateway",
                            },
                        }
                    )
                    + "\n"
                )
                f.write(json.dumps(last_event) + "\n")
            os.utime(path, (now - age_s, now - age_s))

        write(
            os.path.join(sub, "agent-a1.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            10,
        )
        write(os.path.join(wf, "agent-a2.jsonl"), {"type": "progress"}, 30)
        write(
            os.path.join(sub, "agent-a3.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "end_turn"}},
            20,
        )
        write(
            os.path.join(sub, "agent-a4.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            m.AGENT_ACTIVE_WINDOW_S + 60,
        )
        write(
            os.path.join(sub, "agent-a5.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            15,
            cwd=os.path.join(m.STATE_DIR, "worktrees", "x"),
        )
        det = m.ambient_agent_details(prof, "claude")
        check(
            len(det) == 3,
            "3 active agents (tool_use + workflow-nested + worker); end_turn + stale excluded",
        )
        check(m.ambient_live_agents(prof, "claude") == 3, "count wrapper agrees")
        wfa = next((d for d in det if d.get("wf")))
        check(
            wfa["wf"] == "wf_abc-123",
            "workflow sub-agents tagged with their wf id (monitorable)",
        )
        a1 = next((d for d in det if not d["worker"] and d["age_s"] <= 12))
        check(
            a1["label"].startswith("You are AGENT-X")
            and a1["session"] == "sess-1"
            and (a1["branch"] == "main"),
            "details carry mission label + sessionId + branch from the first line",
        )
        check(
            sum((1 for d in det if d["worker"])) == 1,
            "neomax-worker agents flagged (cwd under STATE_DIR) for ledger dedup",
        )
        check(
            m.ambient_live_agents(prof, "codex") == 0,
            "codex profiles -> 0 (no per-agent transcripts)",
        )


def test_ambient_active_mains():
    """MAIN sessions actively mid-turn count as agents (any launcher); idle-at-prompt
    (end_turn final), stale, and neomax-worker sessions don't. Codex mains via fresh
    rollouts, same worker exclusion."""
    print("test_ambient_active_mains")
    import time as _t

    with tempfile.TemporaryDirectory() as st:
        prof = os.path.join(st, ".claude")
        proj = os.path.join(prof, "projects", "-proj")
        os.makedirs(proj)
        now = _t.time()

        def write(path, last_event, age_s, cwd="/workspace/repo"):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": cwd,
                            "message": {"role": "user", "content": "hi"},
                        }
                    )
                    + "\n"
                )
                f.write(json.dumps(last_event) + "\n")
            os.utime(path, (now - age_s, now - age_s))

        write(
            os.path.join(proj, "s1.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            10,
        )
        write(
            os.path.join(proj, "s2.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "end_turn"}},
            10,
        )
        write(
            os.path.join(proj, "s3.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            m.AGENT_ACTIVE_WINDOW_S + 60,
        )
        write(
            os.path.join(proj, "s4.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            10,
            cwd=os.path.join(m.STATE_DIR, "worktrees", "x"),
        )
        write(
            os.path.join(proj, "s1", "subagents", "agent-a.jsonl"),
            {"type": "assistant", "message": {"stop_reason": "tool_use"}},
            10,
        )
        p5 = os.path.join(proj, "s5.jsonl")
        with open(p5, "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "cwd": "/workspace/repo",
                        "message": {"role": "user", "content": "hi"},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {"type": "assistant", "message": {"stop_reason": "end_turn"}}
                )
                + "\n"
            )
            f.write(json.dumps({"type": "bridge-session"}) + "\n")
            f.write(json.dumps({"type": "ai-title"}) + "\n")
        os.utime(p5, (now - 10, now - 10))
        check(
            m.ambient_active_mains(prof, "claude") == 1,
            "1 active main; idle/stale/worker/subagent AND trailing-metadata-idle excluded",
        )
        md = m.ambient_main_details(prof, "claude")
        check(
            md and md[0]["session"] and (md[0]["cwd"] == "/workspace/repo"),
            "main details carry session + cwd (for the Interactive-sessions panel)",
        )
        check(
            md[0]["label"] == "hi",
            "main details carry the task label (first real user msg)",
        )
        cprof = os.path.join(st, ".codex")
        sess = os.path.join(cprof, "sessions", "2026", "06", "11")
        os.makedirs(sess)
        with open(os.path.join(sess, "rollout-1-aaaa.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {"type": "session_meta", "payload": {"cwd": "/workspace/repo"}}
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "fix the build"},
                    }
                )
                + "\n"
            )
        with open(os.path.join(sess, "rollout-2-bbbb.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"cwd": os.path.join(m.STATE_DIR, "worktrees", "y")},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "x"},
                    }
                )
                + "\n"
            )
        with open(os.path.join(sess, "rollout-3-cccc.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {"type": "session_meta", "payload": {"cwd": "/workspace/sample"}}
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "SYSTEM — fleet-orchestrator session",
                        },
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
                + "\n"
            )
            f.write(
                json.dumps({"type": "event_msg", "payload": {"type": "token_count"}})
                + "\n"
            )
        with open(os.path.join(sess, "rollout-4-dddd.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"cwd": "/workspace/sample/worktrees/task-x/app"},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
                + "\n"
            )
        with open(os.path.join(sess, "rollout-5-eeee.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {"type": "session_meta", "payload": {"cwd": "/workspace/repo"}}
                )
                + "\n"
            )
            f.write(
                json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
                + "\n"
            )
            f.write(
                json.dumps({"type": "event_msg", "payload": {"type": "session_end"}})
                + "\n"
            )
        check(
            m.ambient_active_mains(cprof, "codex") == 2,
            "codex: mid-task AND orchestrator-at-task_complete count; STATE_DIR + /worktrees/ workers + session_end excluded",
        )
        cmd_ = m.ambient_main_details(cprof, "codex")
        orch = next((d for d in cmd_ if d["cwd"] == "/workspace/sample"), None)
        check(
            orch is not None and len(orch["session"]) > 0,
            "codex orchestrator (task_complete, project root) surfaces as a live main",
        )
        midt = next((d for d in cmd_ if d["cwd"] == "/workspace/repo"), None)
        check(
            midt is not None and midt["label"] == "fix the build",
            "codex main details carry session id + cwd + label (first user_message)",
        )
        check(
            all(("/worktrees/" not in (d["cwd"] or "") for d in cmd_)),
            "project-local /worktrees/ worker excluded from codex mains",
        )


def test_solo_ambient_visibility():
    """A `cmax solo` session runs on ~/.claude-solo, which is NOT a fleet account — _solo_ambient
    scans it so the solo session's sub-agents (and their tokens) still show on the dashboard."""
    print("test_solo_ambient_visibility")
    saved = m._solo_profile
    with tempfile.TemporaryDirectory() as st:
        solo = os.path.join(st, ".claude-solo")
        agd = os.path.join(
            solo, "projects", "proj", "S9", "subagents", "workflows", "wf_z"
        )
        os.makedirs(agd)
        with open(os.path.join(agd, "agent-q.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "S9",
                        "cwd": "/work",
                        "message": {"role": "user", "content": "do part"},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "S9",
                        "cwd": "/work",
                        "message": {
                            "role": "assistant",
                            "model": "claude-opus-4-8",
                            "usage": {
                                "input_tokens": 50,
                                "output_tokens": 20,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 100,
                            },
                        },
                    }
                )
                + "\n"
            )
        try:
            m._solo_profile = lambda: solo
            (sessions, mains, subs) = m._solo_ambient()
            check(
                subs == 1,
                "solo profile is scanned: its sub-agent is detected (not invisible)",
            )
            ses = next((s for s in sessions if s.get("agents")), None)
            check(
                ses and ses.get("acct_no") == "solo" and (ses.get("solo") is True),
                "solo session tagged acct_no=solo / solo=True (renders as SOLO, not a fleet account)",
            )
            ag = (ses or {}).get("agents", [])
            check(
                len(ag) == 1
                and ag[0]["tok"]["in"] == 50
                and (ag[0]["tok"]["out"] == 20)
                and (ag[0]["tok"]["cache"] == 100),
                "the solo sub-agent's in/out/cache tokens are attached",
            )
            m._solo_profile = lambda: os.path.join(st, "nope")
            check(m._solo_ambient() == ([], 0, 0), "no solo profile -> empty, no crash")
        finally:
            m._solo_profile = saved


def test_workflow_journal_recency():
    """An IN-PROGRESS workflow's WHOLE fleet stays visible: an agent that already finished its turn
    (ends on end_turn, file gone quiet > AGENT_ACTIVE_WINDOW_S) is still shown as `done` so long as
    the workflow's journal.jsonl was written within WORKFLOW_RECENT_S; an agent actively writing is
    `working`. The count (gather_status / _solo_ambient subs) is WORKING-only; the display is all.
    Once the journal goes stale, only genuinely-active agents remain. journal.jsonl is never an agent."""
    print("test_workflow_journal_recency")
    import time

    now = time.time()
    with tempfile.TemporaryDirectory() as st:
        prof = os.path.join(st, ".claude")
        wfd = os.path.join(
            prof, "projects", "proj", "S5", "subagents", "workflows", "wf_x"
        )
        os.makedirs(wfd)

        def write_agent(name, end_turn, mtime):
            p = os.path.join(wfd, name)
            with open(p, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "type": "user",
                            "sessionId": "S5",
                            "cwd": "/work",
                            "message": {"role": "user", "content": "do part"},
                        }
                    )
                    + "\n"
                )
                msg = {
                    "role": "assistant",
                    "model": "claude-opus-4-8",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                }
                if end_turn:
                    msg["stop_reason"] = "end_turn"
                else:
                    msg["stop_reason"] = "tool_use"
                fh.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "sessionId": "S5",
                            "cwd": "/work",
                            "message": msg,
                        }
                    )
                    + "\n"
                )
            os.utime(p, (mtime, mtime))

        def write_journal(mtime):
            p = os.path.join(wfd, "journal.jsonl")
            with open(p, "w") as fh:
                fh.write(json.dumps({"type": "started", "agentId": "a1"}) + "\n")
            os.utime(p, (mtime, mtime))

        write_agent("agent-done.jsonl", True, now - 600)
        write_agent("agent-live.jsonl", False, now - 10)
        write_journal(now - 30)
        det = m.ambient_agent_details(prof, "claude")
        by = {}
        for d in det:
            by.setdefault("working" if d.get("working") else "done", []).append(d)
        check(
            len(det) == 2,
            "live workflow: BOTH the finished and the writing agent are shown (journal.jsonl is not an agent)",
        )
        check(
            len(by.get("working", [])) == 1 and by["working"][0].get("done") is False,
            "the actively-writing agent is marked working",
        )
        check(
            len(by.get("done", [])) == 1 and by["done"][0].get("done") is True,
            "the finished agent of the in-progress workflow is still shown, marked done",
        )
        check(
            all((d.get("wf") == "wf_x" for d in det)), "agents carry their workflow id"
        )
        write_journal(now - (m.WORKFLOW_RECENT_S + 300))
        det2 = m.ambient_agent_details(prof, "claude")
        check(
            len(det2) == 1 and det2[0].get("working") is True,
            "stale journal: the finished agent drops, only the still-writing one remains",
        )


def test_fleet_visibility_at_scale():
    """The registrator (status/dashboard) must SEE every sub-agent across a wide fleet — the
    'check them all and know what's going on' guarantee. Fabricate 8 accounts each running an
    in-progress workflow of 16 sub-agents (12 working, 4 finished-its-turn but kept visible by a
    live journal) = 128 agents, and assert ambient_agent_details enumerates ALL of them, buckets
    working vs done correctly per account, and does it fast (no choke at scale)."""
    print("test_fleet_visibility_at_scale")
    import time

    now = time.time()
    (N_ACCTS, PER, WORKING) = (8, 16, 12)
    with tempfile.TemporaryDirectory() as st:
        profs = []
        for ai in range(N_ACCTS):
            prof = os.path.join(st, "acct%d" % ai)
            profs.append(prof)
            wfd = os.path.join(
                prof,
                "projects",
                "p",
                "S%d" % ai,
                "subagents",
                "workflows",
                "wf_%d" % ai,
            )
            os.makedirs(wfd)
            jp = os.path.join(wfd, "journal.jsonl")
            with open(jp, "w") as fh:
                fh.write(json.dumps({"type": "started", "agentId": "a"}) + "\n")
            os.utime(jp, (now - 30, now - 30))
            for gi in range(PER):
                working = gi < WORKING
                ap = os.path.join(wfd, "agent-%02d.jsonl" % gi)
                with open(ap, "w") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "type": "user",
                                "sessionId": "S%d" % ai,
                                "cwd": "/work",
                                "message": {"role": "user", "content": "slice %d" % gi},
                            }
                        )
                        + "\n"
                    )
                    fh.write(
                        json.dumps(
                            {
                                "type": "assistant",
                                "sessionId": "S%d" % ai,
                                "cwd": "/work",
                                "message": {
                                    "role": "assistant",
                                    "model": "claude-opus-4-8",
                                    "stop_reason": "tool_use"
                                    if working
                                    else "end_turn",
                                    "usage": {
                                        "input_tokens": 1,
                                        "output_tokens": 1,
                                        "cache_creation_input_tokens": 0,
                                        "cache_read_input_tokens": 0,
                                    },
                                },
                            }
                        )
                        + "\n"
                    )
                mt = now - (5 if working else 600)
                os.utime(ap, (mt, mt))
        t0 = time.time()
        seen = working_seen = 0
        per_ok = True
        for prof in profs:
            det = m.ambient_agent_details(prof, "claude")
            if len(det) != PER:
                per_ok = False
            seen += len(det)
            working_seen += sum((1 for d in det if d.get("working")))
        elapsed = time.time() - t0
        check(
            per_ok and seen == N_ACCTS * PER,
            "registrator enumerates EVERY agent across the fleet (%d accts x %d = %d)"
            % (N_ACCTS, PER, N_ACCTS * PER),
        )
        check(
            working_seen == N_ACCTS * WORKING,
            "working vs done is bucketed correctly fleet-wide (%d working)"
            % (N_ACCTS * WORKING),
        )
        check(
            elapsed < 3.0,
            "enumerating %d agents across %d accounts is fast (no choke): %.3fs"
            % (seen, N_ACCTS, elapsed),
        )


def test_subagent_tokens():
    """Per-sub-agent token totals for the Interactive Sessions panel: each USER sub-agent's own
    tokens are summed from its transcript file (sub-agents share the parent's sessionId, so the
    usage ledger can't split them). Worker sub-agents (run in a worktree) are NOT summed here."""
    print("test_subagent_tokens")
    saved_state = m.STATE_DIR
    with tempfile.TemporaryDirectory() as st:
        m.STATE_DIR = os.path.join(st, "state")
        prof = os.path.join(st, ".claude")

        def write_agent(sess, cwd, *usages):
            d = os.path.join(
                prof, "projects", "proj", sess, "subagents", "workflows", "wf_x"
            )
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "agent-%s.jsonl" % sess), "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "type": "user",
                            "sessionId": sess,
                            "cwd": cwd,
                            "message": {"role": "user", "content": "do X"},
                        }
                    )
                    + "\n"
                )
                for i, o, cw, cr in usages:
                    fh.write(
                        json.dumps(
                            {
                                "type": "assistant",
                                "sessionId": sess,
                                "cwd": cwd,
                                "message": {
                                    "role": "assistant",
                                    "model": "claude-opus-4-8",
                                    "usage": {
                                        "input_tokens": i,
                                        "output_tokens": o,
                                        "cache_creation_input_tokens": cw,
                                        "cache_read_input_tokens": cr,
                                    },
                                },
                            }
                        )
                        + "\n"
                    )

        try:
            write_agent("S1", "/work/repo", (100, 50, 10, 200), (80, 40, 0, 300))
            write_agent(
                "S2", os.path.join(m.STATE_DIR, "worktrees", "w1"), (1, 1, 1, 1)
            )
            dets = {d["session"]: d for d in m.ambient_agent_details(prof, "claude")}
            t = dets.get("S1", {}).get("tok") or {}
            check(
                t.get("in") == 100 + 80
                and t.get("out") == 50 + 40
                and (t.get("cache") == 10 + 200 + (0 + 300)),
                "a USER sub-agent's tokens are summed from its file, SPLIT input / output / cache",
            )
            check(
                dets["S1"].get("worker") is False,
                "a repo-cwd sub-agent is not a worker",
            )
            tw = dets["S2"].get("tok") or {}
            check(
                tw.get("in") == 0
                and tw.get("out") == 0
                and (tw.get("cache") == 0)
                and (dets["S2"].get("worker") is True),
                "a WORKER sub-agent (worktree cwd) is not summed here (it's in the run ledger)",
            )
        finally:
            m.STATE_DIR = saved_state


def test_portal_send_disconnect():
    """The dashboard polls every few seconds, so a closed tab or a poll superseded
    by the next refresh routinely drops the socket while the server is mid-write.
    Handler._send must swallow BrokenPipeError/ConnectionResetError (and mark the
    connection closed) so ThreadingHTTPServer doesn't dump a full traceback per
    disconnect — while a healthy client still gets its body and keep-alive, and a
    genuine (non-disconnect) error is NOT hidden."""
    print("test_portal_send_disconnect")
    loader = SourceFileLoader("neomax_portal", os.path.join(BIN, "neomax-portal"))
    spec = importlib.util.spec_from_loader("neomax_portal", loader)
    portal = importlib.util.module_from_spec(spec)
    loader.exec_module(portal)
    check(
        "OpenCode SQLite detail" in portal.PAGE
        and "Kimi local detail" in portal.PAGE
        and ("Grok local detail" in portal.PAGE)
        and ("Claude + Codex + OpenCode + Kimi + Grok" in portal.PAGE),
        "universal portal renders all five engines and local native-agent telemetry",
    )
    check(
        "done / requests" in portal.PAGE
        and "errors / 429" in portal.PAGE
        and ("reasoning tok" in portal.PAGE),
        "portal exposes requests/completions, failures/rate-limits, and reasoning tokens",
    )

    def make_handler(wfile):
        h = portal.Handler.__new__(portal.Handler)
        h.wfile = wfile
        h.send_response = lambda *a, **k: None
        h.send_header = lambda *a, **k: None
        h.end_headers = lambda *a, **k: None
        h.close_connection = False
        return h

    class RaiseWfile:
        def __init__(self, exc):
            self.exc = exc

        def write(self, b):
            raise self.exc

    for exc in (BrokenPipeError("client gone"), ConnectionResetError("reset by peer")):
        h = make_handler(RaiseWfile(exc))
        h._send(200, json.dumps({"ok": True}))
        check(
            h.close_connection is True,
            "portal _send swallows %s and marks the connection closed"
            % type(exc).__name__,
        )
    written = []

    class OkWfile:
        def write(self, b):
            written.append(b)

    h = make_handler(OkWfile())
    h._send(200, '{"ok":true}')
    check(
        written == [b'{"ok":true}'], "portal _send writes the body on a healthy socket"
    )
    check(
        h.close_connection is False,
        "portal _send leaves keep-alive intact on a successful send",
    )

    class BoomWfile:
        def write(self, b):
            raise ValueError("boom")

    h = make_handler(BoomWfile())
    raised = False
    try:
        h._send(200, "{}")
    except ValueError:
        raised = True
    check(raised, "portal _send does not swallow non-disconnect errors")
