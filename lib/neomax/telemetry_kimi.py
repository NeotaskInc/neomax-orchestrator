"""Kimi session and usage telemetry."""


def kimi_telemetry_profiles():
    from . import config as _config
    from . import models as _models

    own = _models.ENGINES["kimi"]["test_env"]
    if _config.os.environ.get(own):
        return _models.engine_profiles("kimi")
    other_test = any(
        (
            _config.os.environ.get(_models.ENGINES[e]["test_env"])
            for e in ("claude", "codex", "opencode", "grok")
        )
    )
    return [] if other_test else _models.engine_profiles("kimi")


def _kimi_wire_summary(path, account, session, agent, cutoff):
    from . import account_auth as _account_auth
    from . import config as _config
    from . import models as _models
    from . import telemetry_opencode as _telemetry_opencode

    result = {
        "tokens": {"in": 0, "out": 0, "cw": 0, "cr": 0},
        "records": [],
        "models": {},
        "requests": 0,
        "completions": 0,
        "errors": 0,
        "rate_limits": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "files": set(),
        "last_active": 0,
    }
    try:
        result["last_active"] = int(_config.os.path.getmtime(path))
        if result["last_active"] < cutoff:
            return result
        lines = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return result
    with lines:
        for line in lines:
            try:
                e = _config.json.loads(line)
            except ValueError:
                continue
            ts = (
                _telemetry_opencode._opencode_epoch(e.get("time") or e.get("timestamp"))
                or result["last_active"]
            )
            if ts < cutoff:
                continue
            if e.get("type") == "usage.record":
                u = e.get("usage") or {}
                model = e.get("model") or _models.KIMI_MODEL
                ni = _telemetry_opencode._opencode_int(u.get("inputOther"))
                no = _telemetry_opencode._opencode_int(u.get("output"))
                cw = _telemetry_opencode._opencode_int(u.get("inputCacheCreation"))
                cr = _telemetry_opencode._opencode_int(u.get("inputCacheRead"))
                rid = "kimi:%s:%s:%s" % (
                    session,
                    agent,
                    _config.hashlib.sha256(line.encode("utf-8", "replace")).hexdigest()[
                        :20
                    ],
                )
                result["records"].append(
                    {
                        "ts": ts,
                        "provider": "kimi",
                        "account": account,
                        "model": model,
                        "id": rid,
                        "kind": "add",
                        "session": session,
                        "agent": agent,
                        "in": ni,
                        "out": no,
                        "cw": cw,
                        "cr": cr,
                        "cost": 0.0,
                    }
                )
                result["requests"] += 1
                result["completions"] += 1
                result["models"][model] = result["models"].get(model, 0) + 1
                for key, value in (("in", ni), ("out", no), ("cw", cw), ("cr", cr)):
                    result["tokens"][key] += value
                continue
            text_line = line.lower()
            if "tool.call" in text_line:
                result["tool_calls"] += 1
            if "tool.result" in text_line and (
                '"error"' in text_line or '"failed"' in text_line
            ):
                result["tool_errors"] += 1
            if '"error"' in text_line:
                result["errors"] += 1
            if _account_auth.OPENCODE_RATE_RE.search(line):
                result["rate_limits"] += 1

            def walk(value):
                if isinstance(value, dict):
                    for k, v in value.items():
                        if k in ("path", "file_path", "filePath") and isinstance(
                            v, str
                        ):
                            result["files"].add(v)
                        else:
                            walk(v)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)

            if "path" in text_line or "file" in text_line:
                walk(e)
    return result


def kimi_profile_snapshot(profile, days=7):
    from . import account_state as _account_state
    from . import config as _config
    from . import telemetry_opencode as _telemetry_opencode

    cutoff = _config.time.time() - days * 86400 if days else 0
    account = _config.os.path.basename(profile)
    index = {}
    try:
        for line in open(_config.os.path.join(profile, "session_index.jsonl")):
            try:
                e = _config.json.loads(line)
            except ValueError:
                continue
            sid = e.get("sessionId") or e.get("session_id")
            if sid:
                index[sid] = e
    except OSError:
        pass
    states = _config.globmod.glob(
        _config.os.path.join(profile, "sessions", "**", "state.json"), recursive=True
    )
    (sessions, records) = ([], [])
    totals = {
        k: 0
        for k in (
            "in",
            "out",
            "cw",
            "cr",
            "requests",
            "completions",
            "errors",
            "rate_limits",
            "sessions",
            "main_sessions",
            "native_subagents",
            "tool_calls",
            "tool_errors",
            "files",
        )
    }
    models = {}
    for state_path in states:
        try:
            state = _config.json.load(open(state_path)) or {}
            mt = int(_config.os.path.getmtime(state_path))
        except (OSError, ValueError, AttributeError):
            continue
        sid = state.get("sessionId") or state.get("id")
        if not sid:
            sid = next(
                (
                    k
                    for (k, v) in index.items()
                    if _config.os.path.abspath(v.get("sessionDir") or "")
                    == _config.os.path.dirname(state_path)
                ),
                _config.os.path.basename(_config.os.path.dirname(state_path)),
            )
        idx = index.get(sid) or {}
        cwd = state.get("workDir") or idx.get("workDir")
        agents = state.get("agents") or {"main": {"type": "main"}}
        agent_rows = []
        session_tokens = {"in": 0, "out": 0, "cw": 0, "cr": 0}
        (sess_last, sess_model) = (mt, None)
        sess_requests = sess_completions = sess_errors = sess_limits = 0
        sess_tools = sess_tool_errors = 0
        sess_files = set()
        for aid, meta in agents.items():
            meta = meta if isinstance(meta, dict) else {}
            candidates = [
                _config.os.path.join(
                    _config.os.path.dirname(state_path), "agents", aid, "wire.jsonl"
                )
            ]
            if meta.get("homedir"):
                candidates.append(_config.os.path.join(meta["homedir"], "wire.jsonl"))
            wire = next((x for x in candidates if _config.os.path.isfile(x)), None)
            if not wire:
                continue
            ws = _kimi_wire_summary(wire, account, sid, aid, cutoff)
            records.extend(ws["records"])
            sess_last = max(sess_last, ws["last_active"])
            sess_requests += ws["requests"]
            sess_completions += ws["completions"]
            sess_errors += ws["errors"]
            sess_limits += ws["rate_limits"]
            sess_tools += ws["tool_calls"]
            sess_tool_errors += ws["tool_errors"]
            sess_files.update(ws["files"])
            for k in session_tokens:
                session_tokens[k] += ws["tokens"][k]
            if ws["models"]:
                sess_model = max(ws["models"], key=ws["models"].get)
                for model, count in ws["models"].items():
                    models[model] = models.get(model, 0) + count
            if meta.get("type") != "main" and ws["last_active"] >= cutoff:
                agent_rows.append(
                    {
                        "id": aid,
                        "parent_id": meta.get("parentAgentId"),
                        "last_active": ws["last_active"],
                        "active": _config.time.time() - ws["last_active"]
                        <= _account_state.AGENT_ACTIVE_WINDOW_S,
                        "model": sess_model,
                        "tokens": ws["tokens"],
                        "requests": ws["requests"],
                        "completions": ws["completions"],
                        "errors": ws["errors"],
                        "rate_limits": ws["rate_limits"],
                        "tool_calls": ws["tool_calls"],
                        "tool_errors": ws["tool_errors"],
                        "files": sorted(ws["files"]),
                    }
                )
        if sess_last < cutoff:
            continue
        sessions.append(
            {
                "id": sid,
                "cwd": cwd,
                "title": state.get("title") or state.get("lastPrompt"),
                "started": _telemetry_opencode._opencode_epoch(state.get("createdAt")),
                "last_active": sess_last,
                "active": _config.time.time() - sess_last
                <= _account_state.AGENT_ACTIVE_WINDOW_S,
                "model": sess_model,
                "tokens": session_tokens,
                "requests": sess_requests,
                "completions": sess_completions,
                "errors": sess_errors,
                "rate_limits": sess_limits,
                "tool_calls": sess_tools,
                "tool_errors": sess_tool_errors,
                "files": sorted(sess_files),
                "agents": agent_rows,
            }
        )
    for s in sessions:
        totals["sessions"] += 1
        totals["main_sessions"] += 1
        totals["native_subagents"] += len(s["agents"])
        for k in ("in", "out", "cw", "cr"):
            totals[k] += s["tokens"][k]
        for k in (
            "requests",
            "completions",
            "errors",
            "rate_limits",
            "tool_calls",
            "tool_errors",
        ):
            totals[k] += s[k]
    totals["files"] = len({p for s in sessions for p in s["files"]})
    return {
        "available": _config.os.path.isdir(profile),
        "source": "kimi-local-wire",
        "account": account,
        "window_days": days,
        "sessions": sessions,
        "records": records,
        "totals": totals,
        "models": [{"model": m, "completions": n} for (m, n) in sorted(models.items())],
    }


def kimi_usage_snapshots(days=30):
    return [kimi_profile_snapshot(p, days) for p in kimi_telemetry_profiles()]
