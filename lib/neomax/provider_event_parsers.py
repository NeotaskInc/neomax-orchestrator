"""OpenCode, Kimi, and Grok event parsing."""


def _opencode_reset_epoch(headers):
    """Extract a usable retry/reset instant from OpenCode API response headers."""
    from . import account_state as _account_state
    from . import config as _config

    if not isinstance(headers, dict):
        return None
    h = {str(k).lower(): v for (k, v) in headers.items()}
    now = _config.time.time()
    for key in ("retry-after-ms", "x-ratelimit-reset-after-ms"):
        try:
            return now + float(h[key]) / 1000.0
        except (KeyError, TypeError, ValueError):
            pass
    if "retry-after" in h:
        value = h["retry-after"]
        try:
            return now + float(value)
        except (TypeError, ValueError):
            try:
                return _config.email.utils.parsedate_to_datetime(str(value)).timestamp()
            except (TypeError, ValueError, OverflowError):
                pass
    for key in ("x-ratelimit-reset", "ratelimit-reset"):
        try:
            return _account_state.normalize_reset_epoch(float(h[key]))
        except (KeyError, TypeError, ValueError):
            pass
    return None


def parse_opencode_events(log_file):
    """Parse `opencode run --format json`: session, tools, tokens, and 429 reset."""
    from . import config as _config
    from . import event_parsers as _event_parsers
    from . import worker_commands as _worker_commands

    out = _event_parsers._new_parse_out()
    children = {}
    saw_stop = False
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
        sid = evt.get("sessionID") or evt.get("session_id")
        if sid and (not out["session_id"]):
            out["session_id"] = sid
        t = evt.get("type")
        part = evt.get("part") or {}
        if t == "text":
            txt = part.get("text") or evt.get("text")
            if txt:
                out["result_text"] = txt
        elif t == "tool_use":
            state = part.get("state") or {}
            cid = part.get("callID") or part.get("id") or "tool-%d" % len(children)
            status = state.get("status") or "running"
            if status in ("completed", "success", "ok"):
                status = "completed"
            elif status in ("failed", "error"):
                status = "error"
            tool = part.get("tool") or "tool"
            children[cid] = {
                "id": cid,
                "kind": "agent"
                if tool in ("task", "general", "explore", "scout")
                else "step",
                "label": (state.get("title") or tool)[:80],
                "status": status,
            }
        elif t == "step_finish":
            reason = part.get("reason") or evt.get("reason")
            if reason == "stop":
                saw_stop = True
            tok = part.get("tokens") or evt.get("tokens") or {}
            cache = tok.get("cache") or {}
            usage = out["usage"]
            for key in ("total", "input", "output", "reasoning"):
                usage[key] = usage.get(key, 0) + int(tok.get(key) or 0)
            usage["cache_write"] = usage.get("cache_write", 0) + int(
                cache.get("write") or 0
            )
            usage["cache_read"] = usage.get("cache_read", 0) + int(
                cache.get("read") or 0
            )
            usage["cost"] = usage.get("cost", 0.0) + float(
                part.get("cost") or evt.get("cost") or 0.0
            )
        elif t == "error":
            er = evt.get("error") or {}
            ed = er.get("data") if isinstance(er, dict) else {}
            ed = ed if isinstance(ed, dict) else {}
            message = (
                ed.get("message")
                or (er.get("message") if isinstance(er, dict) else None)
                or str(er)
            )
            body = ed.get("responseBody") or ""
            status = ed.get("statusCode") or ed.get("status")
            out["errors"].append(str(message))
            if body:
                out["errors"].append(str(body))
            out["api_error_status"] = status
            if str(status) == "429" or _worker_commands.CODEX_LIMIT.search(
                "%s %s" % (message, body)
            ):
                out["rate_limited"] = True
                out["resets_at"] = _opencode_reset_epoch(ed.get("responseHeaders"))
                out["limit_window"] = "provider"
    if saw_stop:
        out["subtype"] = "success"
        for child in children.values():
            if child["status"] == "running":
                child["status"] = "completed"
    else:
        out["is_error"] = True
        out["subtype"] = "error_during_execution" if out["errors"] else "incomplete"
    out["children"] = list(children.values())
    return out


def parse_kimi_events(log_file):
    """Parse Kimi Code stream-json output, including native tool/agent activity."""
    from . import config as _config
    from . import event_parsers as _event_parsers
    from . import worker_commands as _worker_commands

    out = _event_parsers._new_parse_out()
    children = {}
    saw_terminal = False
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
        if evt.get("role") == "meta" and evt.get("type") == "session.resume_hint":
            out["session_id"] = evt.get("session_id") or out["session_id"]
            saw_terminal = True
            continue
        if evt.get("role") == "assistant":
            content = evt.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    (
                        str(x.get("text") or x.get("content") or "")
                        for x in content
                        if isinstance(x, dict)
                    )
                ).strip()
            if isinstance(content, str) and content.strip():
                out["result_text"] = content
            for call in evt.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                cid = call.get("id") or "tool-%d" % len(children)
                name = call.get("name") or fn.get("name") or "tool"
                children[cid] = {
                    "id": cid,
                    "kind": "agent"
                    if name.lower() in ("task", "agent", "agentswarm", "senddmail")
                    else "step",
                    "label": str(name)[:80],
                    "status": "running",
                }
        elif evt.get("role") == "tool":
            cid = evt.get("tool_call_id") or evt.get("id")
            if cid and cid in children:
                children[cid]["status"] = "completed"
        if evt.get("role") in ("error", "system") or evt.get("type") == "error":
            err_obj = evt.get("error")
            containers = [evt]
            if isinstance(err_obj, dict):
                containers.append(err_obj)
                for key in ("data", "response", "cause"):
                    if isinstance(err_obj.get(key), dict):
                        containers.append(err_obj[key])
            status = next(
                (
                    x.get("statusCode") or x.get("status_code") or x.get("status")
                    for x in containers
                    if x.get("statusCode") or x.get("status_code") or x.get("status")
                ),
                None,
            )
            headers = next(
                (
                    x.get("responseHeaders")
                    or x.get("response_headers")
                    or x.get("headers")
                    for x in containers
                    if x.get("responseHeaders")
                    or x.get("response_headers")
                    or x.get("headers")
                ),
                None,
            )
            msg = (
                evt.get("message")
                or (err_obj.get("message") if isinstance(err_obj, dict) else err_obj)
                or evt.get("content")
            )
            if msg:
                out["errors"].append(str(msg))
            if str(status) == "429":
                out["api_error_status"] = status
                out["rate_limited"] = True
                out["resets_at"] = _opencode_reset_epoch(headers)
                out["limit_window"] = "provider"
    blob = " ".join(out["errors"]) + " " + (out["result_text"] or "")
    if _worker_commands.CODEX_LIMIT.search(blob):
        out["rate_limited"] = True
        out["limit_window"] = "provider"
    if saw_terminal and (not out["errors"]):
        out["subtype"] = "success"
        for child in children.values():
            if child["status"] == "running":
                child["status"] = "completed"
    else:
        out["is_error"] = True
        out["subtype"] = "error_during_execution" if out["errors"] else "incomplete"
    out["children"] = list(children.values())
    return out


def parse_grok_events(log_file):
    from . import account_auth as _account_auth
    from . import config as _config
    from . import event_parsers as _event_parsers

    out = _event_parsers._new_parse_out()
    children = {}
    text_parts = []
    saw_end = False
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
        kind = evt.get("type")
        if kind == "text":
            value = evt.get("data")
            if isinstance(value, str):
                text_parts.append(value)
        elif kind == "tool_call":
            cid = evt.get("toolCallId") or "tool-%d" % len(children)
            name = evt.get("toolName") or evt.get("title") or "tool"
            children[cid] = {
                "id": cid,
                "kind": "agent"
                if str(name).lower() in ("task", "agent", "subagent", "spawn_subagent")
                else "step",
                "label": str(evt.get("title") or name)[:80],
                "status": "running",
            }
        elif kind == "tool_call_update":
            cid = evt.get("toolCallId")
            if cid and cid in children:
                status = str(evt.get("status") or "").lower()
                if status in ("failed", "error"):
                    children[cid]["status"] = "error"
                elif status in ("completed", "success", "ok"):
                    children[cid]["status"] = "completed"
        elif kind == "usage":
            usage = evt.get("usage")
            if isinstance(usage, dict):
                out["usage"] = usage
        elif kind == "error":
            msg = str(evt.get("message") or "Grok error")
            out["errors"].append(msg)
            if _account_auth.OPENCODE_RATE_RE.search(msg):
                out["rate_limited"] = True
                out["api_error_status"] = 429
                out["limit_window"] = "provider"
        elif kind == "end":
            saw_end = True
            out["session_id"] = evt.get("sessionId") or out["session_id"]
            usage = evt.get("usage")
            if isinstance(usage, dict):
                out["usage"] = usage
            stop = str(evt.get("stopReason") or "").lower()
            if stop in ("rate_limit", "rate_limited"):
                out["rate_limited"] = True
                out["api_error_status"] = 429
                out["limit_window"] = "provider"
            elif stop in ("error", "cancelled", "refusal"):
                out["is_error"] = True
                out["subtype"] = "error_during_execution"
    out["result_text"] = "".join(text_parts).strip() or None
    blob = " ".join(out["errors"])
    if _account_auth.OPENCODE_RATE_RE.search(blob):
        out["rate_limited"] = True
        out["limit_window"] = "provider"
    if saw_end and (not out["is_error"]) and (not out["rate_limited"]):
        out["subtype"] = "success"
        for child in children.values():
            if child["status"] == "running":
                child["status"] = "completed"
    elif out["subtype"] is None:
        out["is_error"] = True
        out["subtype"] = "error_during_execution" if out["errors"] else "incomplete"
    out["children"] = list(children.values())
    return out
