"""OpenCode session and usage telemetry."""


def opencode_data_dir(profile):
    """The upstream OpenCode data directory represented by a Neomax profile."""
    from . import config as _config
    from . import models as _models

    if _config.os.path.abspath(profile) == _config.os.path.abspath(
        _models.ENGINES["opencode"]["default_dir"]
    ):
        return _config.os.path.join(_config.HOME, ".local", "share", "opencode")
    return _config.os.path.join(_config.os.path.abspath(profile), "opencode")


def opencode_db_path(profile):
    from . import config as _config

    return _config.os.path.join(opencode_data_dir(profile), "opencode.db")


def _opencode_epoch(value):
    """OpenCode stores millisecond epochs; tolerate seconds/floats in fixtures."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return 0
    while n > 100000000000.0:
        n /= 1000.0
    return int(n)


def _opencode_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _opencode_model(value):
    """Session.model JSON or a provider/model pair → a stable catalog slug."""
    from . import config as _config

    if isinstance(value, str):
        try:
            parsed = _config.json.loads(value)
        except (ValueError, TypeError):
            return value
    else:
        parsed = value
    if isinstance(parsed, dict):
        provider = parsed.get("providerID") or parsed.get("provider")
        model = parsed.get("id") or parsed.get("modelID") or parsed.get("model")
        return "%s/%s" % (provider, model) if provider and model else model or provider
    return str(parsed) if parsed else None


def _opencode_error(error):
    """Non-secret, bounded error metadata plus structured rate-limit classification."""
    from . import account_auth as _account_auth

    if not isinstance(error, dict):
        return None
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    status = (
        data.get("statusCode")
        or data.get("status")
        or error.get("statusCode")
        or error.get("status")
    )
    message = (
        data.get("message")
        or error.get("message")
        or error.get("name")
        or "request failed"
    )
    text = "%s %s %s" % (error.get("name") or "", status or "", message)
    return {
        "name": error.get("name") or "Error",
        "status": status,
        "message": " ".join(str(message).split())[:180],
        "rate_limited": str(status) == "429"
        or bool(_account_auth.OPENCODE_RATE_RE.search(text)),
    }


def _opencode_lines(value):
    return str(value).count("\n") + 1 if value not in (None, "") else 0


def opencode_profile_snapshot(profile, days=7):
    """Read one OpenCode profile's SQLite telemetry."""
    from . import account_state as _account_state
    from . import config as _config

    db = opencode_db_path(profile)
    acct = _config.os.path.basename(_config.os.path.abspath(profile))
    result = {
        "available": False,
        "source": "opencode.db",
        "database": db,
        "account": acct,
        "window_days": days,
        "records": [],
        "sessions": [],
        "tool_usage": [],
        "models": [],
        "agents": [],
        "totals": {
            "in": 0,
            "out": 0,
            "reasoning": 0,
            "cw": 0,
            "cr": 0,
            "cost": 0.0,
            "requests": 0,
            "completions": 0,
            "unfinished": 0,
            "errors": 0,
            "rate_limits": 0,
            "sessions": 0,
            "main_sessions": 0,
            "native_subagents": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "files": 0,
            "adds": 0,
            "dels": 0,
            "last_activity": 0,
        },
    }
    if not _config.os.path.isfile(db):
        return result
    cutoff = _config.time.time() - float(days) * 86400 if days else 0
    cutoff_ms = int(cutoff * 1000)
    active_ms = int((_config.time.time() - _account_state.AGENT_ACTIVE_WINDOW_S) * 1000)
    conn = None
    try:
        conn = _config.sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=0.25)
        conn.row_factory = _config.sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        result["available"] = True
        try:
            result["db_bytes"] = _config.os.path.getsize(db)
        except OSError:
            pass
        sql = "SELECT id, project_id, parent_id, directory, title, agent, model,\n                        time_created, time_updated, time_archived,\n                        summary_additions, summary_deletions, summary_files,\n                        tokens_input, tokens_output, tokens_reasoning,\n                        tokens_cache_read, tokens_cache_write, cost\n                   FROM session"
        params = []
        if cutoff_ms:
            sql += " WHERE time_updated >= ?"
            params.append(cutoff_ms)
        sql += " ORDER BY time_updated DESC"
        session_map = {}
        for rr in conn.execute(sql, params):
            row = dict(rr)
            tok = {
                "in": _opencode_int(row.get("tokens_input")),
                "out": _opencode_int(row.get("tokens_output")),
                "reasoning": _opencode_int(row.get("tokens_reasoning")),
                "cr": _opencode_int(row.get("tokens_cache_read")),
                "cw": _opencode_int(row.get("tokens_cache_write")),
            }
            sess = {
                "id": row.get("id"),
                "parent_id": row.get("parent_id"),
                "project_id": row.get("project_id"),
                "cwd": row.get("directory"),
                "title": " ".join(str(row.get("title") or "").split())[:140],
                "agent": row.get("agent"),
                "model": _opencode_model(row.get("model")),
                "started": _opencode_epoch(row.get("time_created")),
                "last_active": _opencode_epoch(row.get("time_updated")),
                "archived": bool(row.get("time_archived")),
                "active": False,
                "tokens": tok,
                "cost": float(row.get("cost") or 0.0),
                "summary_additions": _opencode_int(row.get("summary_additions")),
                "summary_deletions": _opencode_int(row.get("summary_deletions")),
                "summary_files": _opencode_int(row.get("summary_files")),
                "requests": 0,
                "completions": 0,
                "errors": 0,
                "rate_limits": 0,
                "tool_calls": 0,
                "tool_errors": 0,
                "files": [],
            }
            session_map[sess["id"]] = sess
            result["sessions"].append(sess)
        (model_stats, agent_stats) = ({}, {})
        latest_error = None
        msql = "SELECT id, session_id, time_created, time_updated, data FROM message"
        mparams = []
        if cutoff_ms:
            msql += " WHERE time_created >= ?"
            mparams.append(cutoff_ms)
        msql += " ORDER BY time_created"
        profkey = _config.hashlib.sha1(
            _config.os.path.abspath(profile).encode()
        ).hexdigest()[:10]
        for rr in conn.execute(msql, mparams):
            try:
                data = _config.json.loads(rr["data"])
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("role") != "assistant":
                continue
            tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            (ni, no) = (
                _opencode_int(tokens.get("input")),
                _opencode_int(tokens.get("output")),
            )
            nr = _opencode_int(tokens.get("reasoning"))
            (ncr, ncw) = (
                _opencode_int(cache.get("read")),
                _opencode_int(cache.get("write")),
            )
            timing = data.get("time") if isinstance(data.get("time"), dict) else {}
            completed_at = _opencode_epoch(timing.get("completed"))
            created_at = _opencode_epoch(timing.get("created") or rr["time_created"])
            errmeta = _opencode_error(data.get("error"))
            completed = bool(completed_at and (not errmeta))
            (provider_id, model_id) = (data.get("providerID"), data.get("modelID"))
            model = (
                "%s/%s" % (provider_id, model_id)
                if provider_id and model_id
                else model_id or provider_id or "unknown"
            )
            agent = data.get("agent") or "unknown"
            cost = float(data.get("cost") or 0.0)
            record = {
                "ts": completed_at or created_at,
                "provider": "opencode",
                "account": acct,
                "model": model,
                "id": "opencode:%s:%s" % (profkey, rr["id"]),
                "kind": "add",
                "session": rr["session_id"],
                "in": ni,
                "out": no,
                "reasoning": nr,
                "cw": ncw,
                "cr": ncr,
                "cost": cost,
                "requests": 1,
                "completions": 1 if completed else 0,
                "errors": 1 if errmeta else 0,
                "rate_limits": 1 if errmeta and errmeta["rate_limited"] else 0,
                "agent": agent,
            }
            result["records"].append(record)
            ss = session_map.get(rr["session_id"])
            if ss:
                ss["requests"] += 1
                ss["completions"] += record["completions"]
                ss["errors"] += record["errors"]
                ss["rate_limits"] += record["rate_limits"]
                ss["model"] = model
                if (
                    not completed_at
                    and (not errmeta)
                    and (rr["time_updated"] >= active_ms)
                ):
                    ss["active"] = True
            ms = model_stats.setdefault(
                model,
                {
                    "model": model,
                    "requests": 0,
                    "completions": 0,
                    "errors": 0,
                    "rate_limits": 0,
                    "in": 0,
                    "out": 0,
                    "reasoning": 0,
                    "cw": 0,
                    "cr": 0,
                    "cost": 0.0,
                },
            )
            ag = agent_stats.setdefault(
                agent,
                {
                    "agent": agent,
                    "requests": 0,
                    "completions": 0,
                    "errors": 0,
                    "in": 0,
                    "out": 0,
                    "reasoning": 0,
                    "cw": 0,
                    "cr": 0,
                },
            )
            for target in (ms, ag):
                target["requests"] += 1
                target["completions"] += record["completions"]
                target["errors"] += record["errors"]
                target["in"] += ni
                target["out"] += no
                target["reasoning"] += nr
                target["cw"] += ncw
                target["cr"] += ncr
            ms["rate_limits"] += record["rate_limits"]
            ms["cost"] += cost
            if errmeta:
                stamp = completed_at or created_at
                if not latest_error or stamp >= latest_error[0]:
                    latest_error = (stamp, errmeta)
        tools = {}
        file_stats = {}
        psql = "SELECT session_id, time_created, time_updated, data FROM part"
        pparams = []
        if cutoff_ms:
            psql += " WHERE time_created >= ?"
            pparams.append(cutoff_ms)
        for rr in conn.execute(psql, pparams):
            try:
                data = _config.json.loads(rr["data"])
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("type") != "tool":
                continue
            tool = str(data.get("tool") or "unknown")
            state = data.get("state") if isinstance(data.get("state"), dict) else {}
            status = str(state.get("status") or "unknown")
            key = (tool, status)
            tools[key] = tools.get(key, 0) + 1
            ss = session_map.get(rr["session_id"])
            if (
                ss
                and status in ("running", "pending")
                and (rr["time_updated"] >= active_ms)
            ):
                ss["active"] = True
            if ss:
                ss["tool_calls"] += 1
                if status == "error":
                    ss["tool_errors"] += 1
            inp = state.get("input") if isinstance(state.get("input"), dict) else {}
            path = inp.get("filePath") or inp.get("filepath") or inp.get("path")
            if not path or tool not in ("edit", "write"):
                continue
            by_sess = file_stats.setdefault(rr["session_id"], {})
            fs = by_sess.setdefault(
                path, {"path": path, "adds": 0, "dels": 0, "ops": 0}
            )
            fs["ops"] += 1
            if tool == "edit":
                fs["adds"] += _opencode_lines(inp.get("newString"))
                fs["dels"] += _opencode_lines(inp.get("oldString"))
            else:
                fs["adds"] += _opencode_lines(inp.get("content"))
        for sid, stats in file_stats.items():
            if sid in session_map:
                session_map[sid]["files"] = [stats[k] for k in sorted(stats)]
        result["tool_usage"] = [
            {"tool": tool, "status": status, "calls": count}
            for ((tool, status), count) in sorted(
                tools.items(), key=lambda x: (-x[1], x[0])
            )
        ]
        result["models"] = sorted(
            model_stats.values(), key=lambda x: (-x["out"], x["model"])
        )
        result["agents"] = sorted(
            agent_stats.values(), key=lambda x: (-x["out"], x["agent"])
        )
        t = result["totals"]
        for rec in result["records"]:
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
            ):
                t[k] += rec[k]
            t["cost"] += rec["cost"]
        t["unfinished"] = max(0, t["requests"] - t["completions"] - t["errors"])
        t["sessions"] = len(result["sessions"])
        t["main_sessions"] = sum((1 for s in result["sessions"] if not s["parent_id"]))
        t["native_subagents"] = sum((1 for s in result["sessions"] if s["parent_id"]))
        t["tool_calls"] = sum((x["calls"] for x in result["tool_usage"]))
        t["tool_errors"] = sum(
            (x["calls"] for x in result["tool_usage"] if x["status"] == "error")
        )
        all_files = {
            f["path"] for s in result["sessions"] for f in s.get("files") or []
        }
        t["files"] = len(all_files)
        t["adds"] = sum(
            (f["adds"] for s in result["sessions"] for f in s.get("files") or [])
        )
        t["dels"] = sum(
            (f["dels"] for s in result["sessions"] for f in s.get("files") or [])
        )
        t["last_activity"] = max([s["last_active"] for s in result["sessions"]] or [0])
        t["cost"] = round(t["cost"], 6)
        if latest_error:
            result["last_error"] = dict(latest_error[1], at=latest_error[0])
    except (_config.sqlite3.Error, OSError, ValueError, TypeError) as exc:
        result["available"] = False
        result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    finally:
        if conn is not None:
            try:
                conn.close()
            except _config.sqlite3.Error:
                pass
    return result


def opencode_usage_snapshots(days=30):
    """Every configured OpenCode profile's exact local telemetry for a time range."""
    return [opencode_profile_snapshot(p, days) for p in opencode_telemetry_profiles()]


def opencode_telemetry_profiles():
    """OpenCode profiles, including hermetic test overrides."""
    from . import config as _config
    from . import models as _models

    own = _models.ENGINES["opencode"]["test_env"]
    if _config.os.environ.get(own):
        return _models.engine_profiles("opencode")
    other_test = any(
        (
            _config.os.environ.get(_models.ENGINES[e]["test_env"])
            for e in ("claude", "codex", "kimi", "grok")
        )
    )
    return [] if other_test else _models.engine_profiles("opencode")
