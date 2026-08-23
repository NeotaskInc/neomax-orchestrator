"""Grok session and usage telemetry."""


def grok_telemetry_profiles():
    from . import config as _config
    from . import models as _models

    own = _models.ENGINES["grok"]["test_env"]
    if _config.os.environ.get(own):
        return _models.engine_profiles("grok")
    other_test = any(
        (
            _config.os.environ.get(_models.ENGINES[e]["test_env"])
            for e in ("claude", "codex", "opencode", "kimi")
        )
    )
    return [] if other_test else _models.engine_profiles("grok")


def _grok_prompt_usage(update, account, session, model, ts):
    from . import account_auth as _account_auth
    from . import models as _models
    from . import telemetry_opencode as _telemetry_opencode

    u = update.get("usage") or {}
    if not isinstance(u, dict):
        return None
    ni_full = _telemetry_opencode._opencode_int(
        u.get("inputTokens", u.get("input_tokens"))
    )
    no = _telemetry_opencode._opencode_int(
        u.get("outputTokens", u.get("output_tokens"))
    )
    cr = _telemetry_opencode._opencode_int(
        u.get("cachedReadTokens", u.get("cache_read_input_tokens"))
    )
    cw = _telemetry_opencode._opencode_int(
        u.get("cacheCreationTokens", u.get("cache_creation_input_tokens"))
    )
    reasoning = _telemetry_opencode._opencode_int(
        u.get("reasoningTokens", u.get("reasoning_tokens"))
    )
    model_usage = u.get("modelUsage") or {}
    calls = _telemetry_opencode._opencode_int(u.get("modelCalls"))
    if isinstance(model_usage, dict) and model_usage:
        if len(model_usage) == 1:
            model = next(iter(model_usage))
        if not calls:
            calls = sum(
                (
                    _telemetry_opencode._opencode_int((row or {}).get("modelCalls"))
                    for row in model_usage.values()
                    if isinstance(row, dict)
                )
            )
    if not (ni_full or no or cr or cw or calls or model_usage):
        return None
    partial = bool(u.get("usageIsIncomplete") or u.get("costIsPartial"))
    ticks = u.get("costUsdTicks", u.get("total_cost_usd_ticks"))
    cost = None
    if ticks is not None and (not partial):
        try:
            cost = float(ticks) / 10000000000.0
        except (TypeError, ValueError):
            pass
    if cost is None and (not partial) and (u.get("total_cost_usd") is not None):
        try:
            cost = float(u["total_cost_usd"])
        except (TypeError, ValueError):
            pass
    stop = str(update.get("stop_reason") or "").lower()
    rate = stop == "rate_limit" or bool(
        _account_auth.OPENCODE_RATE_RE.search(str(update.get("agent_result") or ""))
    )
    rec = {
        "ts": ts,
        "provider": "grok",
        "account": account,
        "model": model or _models.GROK_MODEL,
        "id": "grok:%s:%s" % (session, update.get("prompt_id") or ts),
        "kind": "add",
        "session": session,
        "in": max(0, ni_full - cr - cw),
        "out": no,
        "reasoning": reasoning,
        "cw": cw,
        "cr": cr,
        "requests": max(calls, 1),
        "completions": 0 if stop in ("error", "rate_limit", "cancelled") else 1,
        "errors": 1 if stop in ("error", "rate_limit") else 0,
        "rate_limits": 1 if rate else 0,
    }
    if cost is not None:
        rec["cost"] = cost
    else:
        rec["cost"] = 0.0
    return rec


def _grok_updates(path, account, session, model, cutoff, include_records=True):
    from . import config as _config
    from . import http_client as _http_client
    from . import telemetry_opencode as _telemetry_opencode

    out = {
        "records": [],
        "tools": 0,
        "tool_errors": 0,
        "files": set(),
        "errors": 0,
        "rate_limits": 0,
        "agents": {},
        "last_active": 0,
    }
    try:
        out["last_active"] = int(_config.os.path.getmtime(path))
        lines = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with lines:
        for raw in lines:
            try:
                env = _config.json.loads(raw)
            except ValueError:
                continue
            ts = (
                _http_client.iso_to_epoch(env.get("timestamp"))
                or _telemetry_opencode._opencode_epoch(env.get("timestamp"))
                or out["last_active"]
            )
            if ts < cutoff:
                continue
            update = (env.get("params") or {}).get("update") or {}
            if not isinstance(update, dict):
                continue
            tag = update.get("sessionUpdate") or ""
            if tag == "turn_completed":
                rec = _grok_prompt_usage(update, account, session, model, ts)
                if rec:
                    out["errors"] += rec.get("errors", 0)
                    out["rate_limits"] += rec.get("rate_limits", 0)
                    if include_records:
                        out["records"].append(rec)
            elif tag == "tool_call":
                out["tools"] += 1
            elif tag == "tool_call_update" and update.get("status") == "failed":
                out["tool_errors"] += 1
            elif tag == "subagent_spawned":
                aid = update.get("subagent_id") or update.get("child_session_id")
                if aid:
                    out["agents"][aid] = {
                        "id": aid,
                        "session": update.get("child_session_id") or aid,
                        "label": update.get("description")
                        or update.get("subagent_type")
                        or "subagent",
                        "model": update.get("model"),
                        "status": "running",
                        "last_active": ts,
                        "tool_calls": 0,
                        "errors": 0,
                    }
            elif tag == "subagent_progress":
                aid = update.get("subagent_id")
                if aid:
                    row = out["agents"].setdefault(
                        aid,
                        {
                            "id": aid,
                            "session": update.get("child_session_id") or aid,
                            "label": "subagent",
                            "status": "running",
                        },
                    )
                    row.update(
                        {
                            "last_active": ts,
                            "tool_calls": _telemetry_opencode._opencode_int(
                                update.get("tool_call_count")
                            ),
                            "errors": _telemetry_opencode._opencode_int(
                                update.get("error_count")
                            ),
                        }
                    )
            elif tag == "subagent_finished":
                aid = update.get("subagent_id")
                if aid:
                    row = out["agents"].setdefault(
                        aid,
                        {
                            "id": aid,
                            "session": update.get("child_session_id") or aid,
                            "label": "subagent",
                        },
                    )
                    row.update(
                        {
                            "status": update.get("status") or "completed",
                            "last_active": ts,
                            "tool_calls": _telemetry_opencode._opencode_int(
                                update.get("tool_calls")
                            ),
                            "errors": 1 if update.get("error") else 0,
                        }
                    )

            def collect(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ("path", "file_path", "filePath") and isinstance(
                            item, str
                        ):
                            out["files"].add(item)
                        else:
                            collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)

            if tag in ("tool_call", "tool_call_update"):
                collect(update.get("rawInput") or update.get("raw_input") or update)
    return out


def grok_profile_snapshot(profile, days=7):
    from . import account_state as _account_state
    from . import config as _config
    from . import http_client as _http_client
    from . import models as _models
    from . import telemetry_opencode as _telemetry_opencode

    cutoff = _config.time.time() - days * 86400 if days else 0
    account = _config.os.path.basename(profile)
    sessions = []
    records = []
    totals = {
        k: 0
        for k in (
            "in",
            "out",
            "reasoning",
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
    summaries = _config.globmod.glob(
        _config.os.path.join(profile, "sessions", "**", "summary.json"), recursive=True
    )
    for summary_path in summaries:
        try:
            summary = _config.json.load(open(summary_path)) or {}
            mt = int(_config.os.path.getmtime(summary_path))
        except (OSError, ValueError, AttributeError):
            continue
        info = summary.get("info") or {}
        sid = info.get("id") or _config.os.path.basename(
            _config.os.path.dirname(summary_path)
        )
        last = (
            _http_client.iso_to_epoch(summary.get("last_active_at"))
            or _http_client.iso_to_epoch(summary.get("updated_at"))
            or mt
        )
        if last < cutoff:
            continue
        model = summary.get("current_model_id") or _models.GROK_MODEL
        parent = summary.get("parent_session_id")
        is_child = bool(
            parent or str(summary.get("session_kind") or "").startswith("subagent")
        )
        updates = _grok_updates(
            _config.os.path.join(
                _config.os.path.dirname(summary_path), "updates.jsonl"
            ),
            account,
            sid,
            model,
            cutoff,
            include_records=not is_child,
        )
        last = max(last, updates["last_active"])
        session_records = updates["records"]
        records.extend(session_records)
        tok = {
            k: sum(
                (_telemetry_opencode._opencode_int(r.get(k)) for r in session_records)
            )
            for k in ("in", "out", "reasoning", "cw", "cr")
        }
        requests = sum(
            (
                _telemetry_opencode._opencode_int(r.get("requests"))
                for r in session_records
            )
        )
        completions = sum(
            (
                _telemetry_opencode._opencode_int(r.get("completions"))
                for r in session_records
            )
        )
        cost = sum((float(r.get("cost") or 0) for r in session_records))
        agents = list(updates["agents"].values())
        sessions.append(
            {
                "id": sid,
                "parent_id": parent,
                "cwd": info.get("cwd"),
                "branch": summary.get("head_branch"),
                "title": summary.get("generated_title")
                or summary.get("session_summary")
                or summary.get("last_turn_summary"),
                "started": _http_client.iso_to_epoch(summary.get("created_at")),
                "last_active": last,
                "active": _config.time.time() - last
                <= _account_state.AGENT_ACTIVE_WINDOW_S,
                "model": model,
                "tokens": tok,
                "requests": requests,
                "completions": completions,
                "errors": updates["errors"],
                "rate_limits": updates["rate_limits"],
                "tool_calls": updates["tools"],
                "tool_errors": updates["tool_errors"],
                "files": sorted(updates["files"]),
                "cost": cost,
                "agents": agents,
            }
        )
        if not is_child:
            totals["main_sessions"] += 1
            totals["native_subagents"] += len(agents)
            for key in ("in", "out", "reasoning", "cw", "cr"):
                totals[key] += tok.get(key, 0)
            totals["requests"] += requests
            totals["completions"] += completions
            totals["errors"] += updates["errors"]
            totals["rate_limits"] += updates["rate_limits"]
        totals["tool_calls"] += updates["tools"]
        totals["tool_errors"] += updates["tool_errors"]
        for r in session_records:
            models[r["model"]] = models.get(r["model"], 0) + r.get("completions", 0)
    totals["sessions"] = len(sessions)
    totals["files"] = len({f for s in sessions for f in s.get("files") or []})
    return {
        "available": _config.os.path.isdir(profile),
        "source": "grok-local-jsonl",
        "account": account,
        "window_days": days,
        "sessions": sessions,
        "records": records,
        "totals": totals,
        "models": [{"model": m, "completions": n} for (m, n) in sorted(models.items())],
    }


def grok_usage_snapshots(days=30):
    return [grok_profile_snapshot(p, days) for p in grok_telemetry_profiles()]
