"""Claude and Codex session metadata parsing."""


def _claude_tail_active(tail):
    """Is this Claude main session mid-turn? Reverse-scan past trailing METADATA records
    (bridge-session / mode / ai-title / summary …) to the last REAL turn event: an
    assistant message ends the turn iff stop_reason == end_turn; a user record (typed
    prompt or tool_result) means a turn is in flight. Unknown → not active."""
    from . import config as _config

    for line in reversed(tail.strip().split("\n")):
        try:
            e = _config.json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t == "assistant":
            return (e.get("message") or {}).get("stop_reason") != "end_turn"
        if t == "user":
            return True
    return False


def _codex_tail_active(tail):
    """Is this Codex session mid-task? Reverse-scan to the last decisive event:
    task_complete/turn-end → idle; task_started/user_message/exec/item events →
    working. token_count / session_meta / response_item etc. are skipped."""
    from . import config as _config

    IDLE = (
        "task_complete",
        "turn.completed",
        "turn.failed",
        "turn_aborted",
        "shutdown_complete",
        "session_end",
    )
    BUSY = (
        "task_started",
        "turn.started",
        "user_message",
        "exec_command_begin",
        "exec_approval_request",
        "item.started",
        "item.updated",
        "agent_reasoning_delta",
        "agent_message_delta",
        "mcp_tool_call_begin",
    )
    for line in reversed(tail.strip().split("\n")):
        try:
            e = _config.json.loads(line)
        except ValueError:
            continue
        p = e.get("payload") or {}
        t = p.get("type") or e.get("type")
        if t in IDLE:
            return False
        if t in BUSY:
            return True
    return False


def _codex_session_live(tail):
    """Is this Codex session LIVE (window open / autonomously working)? LOOSER than
    _codex_tail_active's 'mid-turn': a finished turn (task_complete / turn.completed) does
    NOT end the session — an interactive window stays open and an autonomous orchestrator
    keeps going between turns, so its rollout's last event is routinely task_complete. ONLY
    an explicit session_end / shutdown_complete means the session is gone. Paired with the
    freshness (mtime) gate at the call site, this is what counts the Codex ORCHESTRATOR as a
    live session/agent on the dashboard (it was vanishing the moment a turn completed)."""
    from . import config as _config

    for line in reversed(tail.strip().split("\n")):
        try:
            e = _config.json.loads(line)
        except ValueError:
            continue
        p = e.get("payload") or {}
        t = p.get("type") or e.get("type")
        if not t:
            continue
        return t not in ("shutdown_complete", "session_end")
    return False


def _claude_head_meta(head):
    """cwd/branch/slug + a task label from a Claude transcript head: the FIRST record
    carrying cwd (compacted sessions open with summary lines that have none), and the
    first real typed user message (skipping caveats / command / system-reminder noise)."""
    from . import config as _config

    meta = {"cwd": None, "branch": None, "slug": None, "label": None}
    for line in head.split("\n"):
        if meta["cwd"] and meta["label"]:
            break
        try:
            e = _config.json.loads(line)
        except ValueError:
            continue
        if not meta["cwd"] and e.get("cwd"):
            meta["cwd"] = e.get("cwd")
            meta["branch"] = e.get("gitBranch")
            meta["slug"] = e.get("slug")
        if not meta["label"] and e.get("type") == "user":
            c = (e.get("message") or {}).get("content")
            if isinstance(c, list):
                c = next(
                    (b.get("text") for b in c if isinstance(b, dict) and b.get("text")),
                    None,
                )
            txt = " ".join(str(c or "").split())
            if txt and (
                not txt.startswith(
                    (
                        "Caveat:",
                        "<command-",
                        "<local-command",
                        "<system-reminder",
                        "<task-notification",
                    )
                )
            ):
                meta["label"] = txt[:110]
    return meta


def _codex_head_meta(head):
    """cwd + a task label (first user_message) from a Codex rollout head."""
    from . import config as _config

    meta = {"cwd": None, "branch": None, "slug": None, "label": None}
    for line in head.split("\n"):
        if meta["cwd"] and meta["label"]:
            break
        try:
            e = _config.json.loads(line)
        except ValueError:
            continue
        p = e.get("payload") or {}
        if not meta["cwd"] and (p.get("cwd") or e.get("cwd")):
            meta["cwd"] = p.get("cwd") or e.get("cwd")
        if not meta["label"] and p.get("type") == "user_message":
            txt = " ".join(str(p.get("message") or "").split())
            if txt:
                meta["label"] = txt[:110]
    return meta
