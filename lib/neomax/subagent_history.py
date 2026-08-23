"""Subagent history and targeted diff reporting."""


def gather_subagent_history(days=3, limit=80):
    """History of INTERACTIVE sessions' finished sub-agents (Agent-tool fleets +
    workflows — work NOT dispatched by the orchestrator, which lives in the run
    ledger/history). Read from the sub-agent transcripts that persist on disk under
    projects/<proj>/<sess>/subagents/**. Each item: when it ran, which session/account,
    its mission, status, the FILES it edited with approximate +adds/-dels (from its
    Edit/Write tool calls), and its workflow id if Workflow-spawned."""
    from . import account_state as _account_state
    from . import config as _config
    from . import diff_parsers as _diff_parsers
    from . import http_client as _http_client
    from . import models as _models
    from . import projects as _projects
    from . import runs as _runs
    from . import status_helpers as _status_helpers
    from . import telemetry_grok as _telemetry_grok
    from . import telemetry_kimi as _telemetry_kimi
    from . import telemetry_opencode as _telemetry_opencode

    cutoff = _config.time.time() - days * 86400
    out = []
    for prof in _models.engine_profiles("claude"):
        for f in _config.globmod.iglob(
            _config.os.path.join(
                prof, "projects", "*", "*", "subagents", "**", "*.jsonl"
            ),
            recursive=True,
        ):
            try:
                mt = _config.os.path.getmtime(f)
                if mt < cutoff:
                    continue
                size = _config.os.path.getsize(f)
            except OSError:
                continue
            (label, cwd, started, last) = (None, None, None, None)
            try:
                with open(f, "rb") as fh:
                    head = fh.read(8192).decode("utf-8", "replace")
                    fh.seek(max(0, size - 65536))
                    tail = fh.read().decode("utf-8", "replace")
            except OSError:
                continue
            try:
                first = _config.json.loads(head.split("\n", 1)[0])
                cwd = first.get("cwd")
                started = _http_client.iso_to_epoch(first.get("timestamp"))
                c = (first.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = next(
                        (
                            bb.get("text")
                            for bb in c
                            if isinstance(bb, dict) and bb.get("text")
                        ),
                        None,
                    )
                label = " ".join(str(c or "").split())[:110] or None
            except (ValueError, AttributeError):
                pass
            for line in tail.split("\n"):
                if '"assistant"' in line:
                    try:
                        last = _config.json.loads(line)
                    except ValueError:
                        pass
            ex = _diff_parsers.extract_claude_exact_patches(f)
            stats = {
                k: {"adds": v["adds"], "dels": v["dels"], "ops": len(v["hunks"])}
                for (k, v) in ex["files"].items()
            }
            if (cwd or "").startswith(_config.STATE_DIR):
                continue
            done = bool(
                last and (last.get("message") or {}).get("stop_reason") == "end_turn"
            )
            active = (
                _config.time.time() - mt <= _account_state.AGENT_ACTIVE_WINDOW_S
                and (not done)
            )
            m_wf = _config.re.search("/subagents/workflows/(wf_[^/]+)/", f)
            sess_dir = f.split("/subagents/")[0]
            out.append(
                {
                    "engine": "claude",
                    "acct_no": _status_helpers.profile_acct_no(prof, "claude"),
                    "session": _config.os.path.basename(sess_dir),
                    "project": _projects.project_of(cwd),
                    "agent": _config.os.path.basename(f)[:-6],
                    "wf": m_wf.group(1) if m_wf else None,
                    "label": label,
                    "cwd": cwd,
                    "started": int(started) if started else None,
                    "ended": int(mt),
                    "status": "running" if active else "done" if done else "stopped",
                    "files": [
                        {
                            "path": k,
                            "adds": v["adds"],
                            "dels": v["dels"],
                            "ops": v["ops"],
                        }
                        for (k, v) in sorted(stats.items())
                    ],
                    "adds": sum((v["adds"] for v in stats.values())),
                    "dels": sum((v["dels"] for v in stats.values())),
                    "size_kb": int(size / 1024),
                }
            )
    worker_sessions = {
        r.get("session")
        for r in _runs.all_runs()
        if r.get("engine") == "opencode" and r.get("session")
    }
    for prof in _telemetry_opencode.opencode_telemetry_profiles():
        snap = _telemetry_opencode.opencode_profile_snapshot(prof, days)
        for s in snap.get("sessions") or []:
            if not s.get("parent_id") or s.get("parent_id") in worker_sessions:
                continue
            cwd = s.get("cwd") or ""
            if (
                cwd.startswith(_config.STATE_DIR)
                or _config.os.sep + "worktrees" + _config.os.sep in cwd
            ):
                continue
            files = s.get("files") or []
            adds = sum((f.get("adds", 0) for f in files)) or s.get(
                "summary_additions", 0
            )
            dels = sum((f.get("dels", 0) for f in files)) or s.get(
                "summary_deletions", 0
            )
            active = bool(s.get("active"))
            if active:
                status = "running"
            elif s.get("completions") and (not s.get("errors")):
                status = "done"
            else:
                status = "stopped"
            tok = s.get("tokens") or {}
            out.append(
                {
                    "engine": "opencode",
                    "acct_no": _status_helpers.profile_acct_no(prof, "opencode"),
                    "account": _config.os.path.basename(prof),
                    "session": s.get("parent_id"),
                    "project": _projects.project_of(cwd),
                    "agent": s.get("id"),
                    "wf": None,
                    "label": s.get("title")
                    or s.get("agent")
                    or "OpenCode native subagent",
                    "cwd": cwd,
                    "model": s.get("model"),
                    "started": s.get("started"),
                    "ended": s.get("last_active"),
                    "status": status,
                    "files": files,
                    "adds": adds,
                    "dels": dels,
                    "tokens": tok,
                    "requests": s.get("requests", 0),
                    "completions": s.get("completions", 0),
                    "errors": s.get("errors", 0),
                    "rate_limits": s.get("rate_limits", 0),
                    "tool_calls": s.get("tool_calls", 0),
                    "tool_errors": s.get("tool_errors", 0),
                    "size_kb": 0,
                }
            )
    kimi_workers = {
        r.get("session")
        for r in _runs.all_runs()
        if r.get("engine") == "kimi" and r.get("session")
    }
    for prof in _telemetry_kimi.kimi_telemetry_profiles():
        snap = _telemetry_kimi.kimi_profile_snapshot(prof, days)
        for s in snap.get("sessions") or []:
            if s.get("id") in kimi_workers:
                continue
            cwd = s.get("cwd") or ""
            if (
                cwd.startswith(_config.STATE_DIR)
                or _config.os.sep + "worktrees" + _config.os.sep in cwd
            ):
                continue
            for a in s.get("agents") or []:
                files = [
                    {"path": p, "adds": 0, "dels": 0, "ops": 0}
                    for p in a.get("files") or []
                ]
                out.append(
                    {
                        "engine": "kimi",
                        "acct_no": _status_helpers.profile_acct_no(prof, "kimi"),
                        "account": _config.os.path.basename(prof),
                        "session": s.get("id"),
                        "project": _projects.project_of(cwd),
                        "agent": a.get("id"),
                        "wf": None,
                        "label": a.get("id") or "Kimi subagent",
                        "cwd": cwd,
                        "model": a.get("model"),
                        "started": s.get("started"),
                        "ended": a.get("last_active"),
                        "status": "running" if a.get("active") else "done",
                        "files": files,
                        "adds": 0,
                        "dels": 0,
                        "tokens": a.get("tokens") or {},
                        "requests": a.get("requests", 0),
                        "completions": a.get("completions", 0),
                        "errors": a.get("errors", 0),
                        "rate_limits": a.get("rate_limits", 0),
                        "tool_calls": a.get("tool_calls", 0),
                        "tool_errors": a.get("tool_errors", 0),
                        "size_kb": 0,
                    }
                )
    grok_workers = {
        r.get("session")
        for r in _runs.all_runs()
        if r.get("engine") == "grok" and r.get("session")
    }
    for prof in _telemetry_grok.grok_telemetry_profiles():
        snap = _telemetry_grok.grok_profile_snapshot(prof, days)
        for s in snap.get("sessions") or []:
            if s.get("parent_id") or s.get("id") in grok_workers:
                continue
            cwd = s.get("cwd") or ""
            if (
                cwd.startswith(_config.STATE_DIR)
                or _config.os.sep + "worktrees" + _config.os.sep in cwd
            ):
                continue
            for a in s.get("agents") or []:
                child = next(
                    (
                        x
                        for x in snap.get("sessions") or []
                        if x.get("id") == a.get("session")
                    ),
                    {},
                )
                files = [
                    {"path": p, "adds": 0, "dels": 0, "ops": 0}
                    for p in child.get("files") or []
                ]
                out.append(
                    {
                        "engine": "grok",
                        "acct_no": _status_helpers.profile_acct_no(prof, "grok"),
                        "account": _config.os.path.basename(prof),
                        "session": s.get("id"),
                        "project": _projects.project_of(cwd),
                        "agent": a.get("id"),
                        "wf": None,
                        "label": a.get("label") or "Grok subagent",
                        "cwd": cwd,
                        "model": a.get("model") or child.get("model"),
                        "started": child.get("started") or s.get("started"),
                        "ended": a.get("last_active"),
                        "status": "running" if a.get("status") == "running" else "done",
                        "files": files,
                        "adds": 0,
                        "dels": 0,
                        "tokens": child.get("tokens") or {},
                        "requests": child.get("requests", 0),
                        "completions": child.get("completions", 0),
                        "errors": child.get("errors", 0),
                        "rate_limits": child.get("rate_limits", 0),
                        "tool_calls": child.get("tool_calls", 0),
                        "tool_errors": child.get("tool_errors", 0),
                        "size_kb": 0,
                    }
                )
    out.sort(key=lambda r: -(r["ended"] or 0))
    return out[:limit]


def cmd_subagents(argv):
    """History of interactive sessions' sub-agents (Agent fleets + workflows) with the
    files each edited (+adds/-dels). `neomax subagents [--json] [--days N]`."""
    from . import config as _config

    days = 3
    if "--days" in argv:
        try:
            days = int(argv[argv.index("--days") + 1])
        except (ValueError, IndexError):
            pass
    rows = gather_subagent_history(days)
    if "--json" in argv:
        print(_config.json.dumps(rows))
        return
    print(
        "SUB-AGENT HISTORY — last %dd (interactive sessions; orchestrator runs live in the run ledger)"
        % days
    )
    for r in rows:
        print(
            "  %s %s acct%s %s %s%s · %d files +%d/-%d · %s"
            % (
                _config.time.strftime(
                    "%m-%d %H:%M", _config.time.localtime(r["ended"])
                ),
                r.get("engine", "claude"),
                r["acct_no"],
                r["session"][:8],
                r["status"],
                " [" + r["wf"] + "]" if r.get("wf") else "",
                len(r["files"]),
                r["adds"],
                r["dels"],
                (r.get("label") or "")[:60],
            )
        )


def cmd_subagent_diff(argv):
    """EXACT per-file diffs of a single sub-agent (or any session transcript), from the
    structuredPatch results + tool_use inputs the CLI recorded. Identical to what was
    applied. `neomax subagent-diff <agent-id|session-id> [--json] [--patch]`."""
    from . import config as _config
    from . import diff_parsers as _diff_parsers
    from . import models as _models

    if not argv:
        _models.err("neomax: subagent-diff <agent-id|session-id> [--json] [--patch]")
        _config.sys.exit(2)
    ident = argv[0]
    matches = []
    for prof in _models.engine_profiles("claude"):
        matches += _config.globmod.glob(
            _config.os.path.join(prof, "projects", "**", ident + ".jsonl"),
            recursive=True,
        )
    if not matches:
        _models.err("neomax: no transcript for %s" % ident)
        _config.sys.exit(1)
    f = max(matches, key=_config.os.path.getsize)
    ex = _diff_parsers.extract_claude_exact_patches(f)
    files = [
        {
            "path": k,
            "adds": v["adds"],
            "dels": v["dels"],
            "patch": "\n".join(v["hunks"]),
        }
        for (k, v) in sorted(ex["files"].items())
    ]
    if "--json" in argv:
        print(
            _config.json.dumps(
                {
                    "id": ident,
                    "n_edits": ex["n_edits"],
                    "files": files,
                    "adds": sum((x["adds"] for x in files)),
                    "dels": sum((x["dels"] for x in files)),
                }
            )
        )
        return
    print("subagent-diff %s — %d edits, %d files" % (ident, ex["n_edits"], len(files)))
    for x in files:
        print("  +%-5d -%-5d %s" % (x["adds"], x["dels"], x["path"]))
        if "--patch" in argv:
            print(x["patch"] + "\n")
