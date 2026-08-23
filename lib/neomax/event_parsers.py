"""Shared, Claude, and Codex event parsing."""


def _new_parse_out():
    return {
        "result_text": None,
        "is_error": False,
        "subtype": None,
        "api_error_status": None,
        "rate_limited": False,
        "resets_at": None,
        "limit_window": None,
        "session_id": None,
        "errors": [],
        "children": [],
        "usage": {},
    }


def parse_events(log_file, engine="claude"):
    from . import provider_event_parsers as _provider_event_parsers

    if engine == "codex":
        return parse_codex_events(log_file)
    if engine == "opencode":
        return _provider_event_parsers.parse_opencode_events(log_file)
    if engine == "kimi":
        return _provider_event_parsers.parse_kimi_events(log_file)
    if engine == "grok":
        return _provider_event_parsers.parse_grok_events(log_file)
    return parse_claude_events(log_file)


def parse_claude_events(log_file):
    """Scan the Claude stream-json log for the fields that matter (+ sub-agents)."""
    from . import config as _config

    out = _new_parse_out()
    children = {}
    try:
        with open(log_file, "rb") as f:
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return out
    for line in data.splitlines():
        try:
            evt = _config.json.loads(line)
        except ValueError:
            continue
        if not isinstance(evt, dict):
            continue
        t = evt.get("type")
        if out["session_id"] is None and evt.get("session_id"):
            out["session_id"] = evt["session_id"]
        if t == "rate_limit_event":
            info = evt.get("rate_limit_info") or {}
            if info.get("status") == "rejected":
                out["rate_limited"] = True
                out["resets_at"] = info.get("resetsAt")
                out["limit_window"] = info.get("rateLimitType")
        elif t == "assistant" and evt.get("error") == "rate_limit":
            out["rate_limited"] = True
        elif t == "system" and evt.get("subtype") in (
            "task_started",
            "task_progress",
            "task_notification",
        ):
            cid = evt.get("task_id") or evt.get("uuid") or evt.get("subtype")
            existing = children.get(cid, {})
            label = (
                evt.get("description")
                or evt.get("subagent_type")
                or existing.get("label")
                or evt.get("subtype")
                or ""
            )
            usage = evt.get("usage") or {}
            children[cid] = {
                "id": cid,
                "label": label[:80],
                "status": "completed"
                if evt.get("subtype") == "task_notification"
                else existing.get("status", "running"),
                "kind": evt.get("task_type")
                or evt.get("workflow_name")
                or existing.get("kind"),
                "last_tool": evt.get("last_tool_name") or existing.get("last_tool"),
                "tokens": usage.get("total_tokens") or existing.get("tokens") or 0,
            }
        elif t == "result":
            out["subtype"] = evt.get("subtype")
            out["is_error"] = bool(evt.get("is_error"))
            out["api_error_status"] = evt.get("api_error_status")
            if evt.get("api_error_status") == 429:
                out["rate_limited"] = True
            txt = evt.get("result")
            if not txt and evt.get("errors"):
                txt = "; ".join((str(e) for e in evt["errors"]))
            out["result_text"] = txt
    if out["subtype"] == "success" and (not out["is_error"]):
        for c in children.values():
            if c["status"] == "running":
                c["status"] = "completed"
    out["children"] = list(children.values())
    return out


def parse_codex_events(log_file):
    """Scan the `codex exec --json` JSONL log: thread_id, final agent text,
    success/failure, rate-limit/auth signals, and per-item children."""
    from . import config as _config
    from . import worker_commands as _worker_commands

    out = _new_parse_out()
    saw_completed = False
    saw_failed = False
    children = []
    try:
        with open(log_file, "rb") as f:
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return out
    for line in data.splitlines():
        try:
            evt = _config.json.loads(line)
        except ValueError:
            continue
        if not isinstance(evt, dict):
            continue
        t = evt.get("type")
        if t == "thread.started" and evt.get("thread_id"):
            out["session_id"] = evt["thread_id"]
        elif t in ("item.started", "item.completed", "item.updated"):
            item = evt.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message":
                if t == "item.completed":
                    out["result_text"] = item.get("text") or out["result_text"]
                continue
            if itype not in (
                "command_execution",
                "tool_call",
                "mcp_tool_call",
                "collab_agent",
                "subagent",
                "agent_run",
                "task",
            ):
                continue
            kind = (
                "step"
                if itype in ("command_execution", "tool_call", "mcp_tool_call")
                else "agent"
            )
            cid = item.get("id") or "item-%d" % len(children)
            if t == "item.started":
                cstatus = "running"
            else:
                st = item.get("status")
                if st in ("failed", "error"):
                    cstatus = "error"
                elif st in ("completed", "success", "ok"):
                    cstatus = "completed"
                elif "exit_code" in item:
                    cstatus = (
                        "completed" if item.get("exit_code") in (0, None) else "error"
                    )
                else:
                    cstatus = "completed"
            cur = next((c for c in children if c["id"] == cid), None)
            if cur:
                cur["status"] = cstatus
            else:
                children.append(
                    {
                        "id": cid,
                        "kind": kind,
                        "label": (
                            item.get("command") or item.get("name") or itype or ""
                        )[:80],
                        "status": cstatus,
                    }
                )
        elif t == "turn.completed":
            saw_completed = True
        elif t == "turn.failed":
            saw_failed = True
            msg = (evt.get("error") or {}).get("message") or _config.json.dumps(
                evt.get("error") or {}
            )
            out["errors"].append(msg)
        elif t == "error":
            out["errors"].append(str(evt.get("message") or ""))
    if saw_completed or saw_failed:
        for c in children:
            if c["status"] == "running":
                c["status"] = "completed" if not saw_failed else "error"
    out["children"] = children
    blob = " ".join(out["errors"]) + " " + (out["result_text"] or "")
    if saw_completed and (not saw_failed):
        out["subtype"] = "success"
    elif saw_failed:
        out["is_error"] = True
        out["subtype"] = "error_during_execution"
        if _worker_commands.CODEX_LIMIT.search(blob):
            out["rate_limited"] = True
    else:
        out["is_error"] = True
        out["subtype"] = "incomplete"
        if _worker_commands.CODEX_LIMIT.search(blob):
            out["rate_limited"] = True
    return out
