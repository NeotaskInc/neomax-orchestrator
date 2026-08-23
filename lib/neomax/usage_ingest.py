"""Provider session-log usage ingestion."""

from . import config as _config


def _ledger_append(records):
    """Append usage records to the ledger, DATE-PARTITIONED by each record's REAL
    completion date (so 7d/30d/all + by-date are correct). Best-effort."""
    from . import config as _config

    if not records:
        return
    try:
        _config.os.makedirs(_config.USAGE_LEDGER_DIR, exist_ok=True)
    except OSError:
        return
    bydate = {}
    for r in records:
        day = _config.time.strftime(
            "%Y-%m-%d", _config.time.localtime(r.get("ts") or _config.time.time())
        )
        bydate.setdefault(day, []).append(r)
    for day, recs in bydate.items():
        try:
            with open(
                _config.os.path.join(_config.USAGE_LEDGER_DIR, day + ".jsonl"), "a"
            ) as f:
                for r in recs:
                    f.write(_config.json.dumps(r) + "\n")
        except OSError:
            pass


def _line_ts(e):
    """Real completion epoch from a transcript/rollout line's ISO `timestamp`; falls back
    to now() only if absent (so date filters reflect when work ACTUALLY happened)."""
    from . import config as _config
    from . import http_client as _http_client

    return _http_client.iso_to_epoch(e.get("timestamp")) or int(_config.time.time())


def _claude_records_from_line(line, account):
    """A Claude transcript line → a usage record (or None). The dedup `id` is the API
    response id (message.id / requestId) — the actual BILLING key — NOT the transcript line
    `uuid`: Claude Code writes ONE billed response as several transcript lines (one per content
    block: thinking / text / tool_use), each repeating the SAME message-level usage, each with
    its own uuid. Deduping by uuid counts a multi-block turn 2-3×; message.id collapses them to
    the single charge. ts = the message's real timestamp; session = sessionId."""
    from . import config as _config

    if '"usage"' not in line or '"assistant"' not in line:
        return None
    try:
        e = _config.json.loads(line)
    except ValueError:
        return None
    msg = e.get("message") or {}
    u = msg.get("usage") or {}
    if msg.get("role") != "assistant" or not u:
        return None
    mid = msg.get("id") or e.get("requestId") or e.get("uuid")
    if not mid:
        return None
    model = msg.get("model") or _config.CLAUDE_DEFAULT_MODEL
    if model == "<synthetic>":
        return None
    return {
        "ts": int(_line_ts(e)),
        "provider": "claude",
        "account": account,
        "model": model,
        "id": mid,
        "kind": "add",
        "session": e.get("sessionId"),
        "in": int(u.get("input_tokens") or 0),
        "out": int(u.get("output_tokens") or 0),
        "cw": int(u.get("cache_creation_input_tokens") or 0),
        "cr": int(u.get("cache_read_input_tokens") or 0),
    }


_CODEX_MODEL_RE = _config.re.compile('"model"\\s*:\\s*"(gpt-[0-9][^"]*)"')


def codex_model_in_line(line):
    """The gpt-* model named on a rollout line, if any (the session_meta / turn_context lines
    carry it; token_count lines do not). Lets the usage scan price a session at the model it
    ACTUALLY ran on instead of assuming the current default — which matters across a family
    switch (gpt-5.5 → gpt-5.6), when both appear in the same history."""
    m = _CODEX_MODEL_RE.search(line)
    return m.group(1) if m else None


def _codex_record_from_line(line, account, session_id, model=None):
    """A Codex rollout line → a CUMULATIVE usage record (or None). token_count.info
    .total_token_usage is the session running total; id = session (dedup → take max); ts =
    this event's real timestamp. `in` is non-cached input (total − cached); cr = cached.
    `model` = the model sniffed from this session's rollout header; falls back to the current
    Codex default when the scan started past that header (incremental read of an old file)."""
    from . import config as _config

    if '"token_count"' not in line or '"total_token_usage"' not in line:
        return None
    try:
        e = _config.json.loads(line)
    except ValueError:
        return None
    p = e.get("payload") or e
    if p.get("type") != "token_count":
        return None
    t = (p.get("info") or {}).get("total_token_usage") or {}
    in_tot = int(t.get("input_tokens") or 0)
    cached = int(t.get("cached_input_tokens") or 0)
    return {
        "ts": int(_line_ts(e)),
        "provider": "codex",
        "account": account,
        "model": model or _config.CODEX_MODEL,
        "id": session_id,
        "kind": "total",
        "session": session_id,
        "in": max(0, in_tot - cached),
        "out": int(t.get("output_tokens") or 0),
        "cw": 0,
        "cr": cached,
    }


def _kimi_record_from_line(line, account, session_id, agent_id):
    from . import config as _config
    from . import models as _models
    from . import telemetry_opencode as _telemetry_opencode

    if '"usage.record"' not in line:
        return None
    try:
        e = _config.json.loads(line)
    except ValueError:
        return None
    if e.get("type") != "usage.record":
        return None
    u = e.get("usage") or {}
    ts = _telemetry_opencode._opencode_epoch(
        e.get("time") or e.get("timestamp")
    ) or int(_config.time.time())
    model = e.get("model") or _models.KIMI_MODEL
    return {
        "ts": ts,
        "provider": "kimi",
        "account": account,
        "model": model,
        "id": "kimi:%s:%s:%s"
        % (
            session_id,
            agent_id,
            _config.hashlib.sha256(line.encode("utf-8", "replace")).hexdigest()[:20],
        ),
        "kind": "add",
        "session": session_id,
        "agent": agent_id,
        "in": _telemetry_opencode._opencode_int(u.get("inputOther")),
        "out": _telemetry_opencode._opencode_int(u.get("output")),
        "cw": _telemetry_opencode._opencode_int(u.get("inputCacheCreation")),
        "cr": _telemetry_opencode._opencode_int(u.get("inputCacheRead")),
        "cost": 0.0,
    }


USAGE_RECENT_DAYS = 2


def _usage_session_files(since_ts=0):
    """(file, account, provider, session descriptor) for local engine transcripts
    modified since `since_ts` (0 = all). Bounding by mtime keeps each pass cheap — we only
    look at recently-active sessions."""
    from . import config as _config
    from . import models as _models
    from . import solo as _solo
    from . import telemetry_kimi as _telemetry_kimi

    out = []
    for engine, sub in (("claude", "projects"), ("codex", "sessions")):
        profs = _models.engine_profiles(engine)
        if engine == "claude":
            solo = _solo._solo_profile()
            if _config.os.path.isdir(solo) and solo not in profs:
                profs = list(profs) + [solo]
        for p in profs:
            acct = _config.os.path.basename(p)
            for f in _config.globmod.glob(
                _config.os.path.join(p, sub, "**", "*.jsonl"), recursive=True
            ):
                try:
                    if since_ts and _config.os.path.getmtime(f) < since_ts:
                        continue
                except OSError:
                    continue
                out.append(
                    (
                        f,
                        acct,
                        engine,
                        _config.os.path.basename(f) if engine == "codex" else None,
                    )
                )
    for p in _telemetry_kimi.kimi_telemetry_profiles():
        acct = _config.os.path.basename(p)
        for f in _config.globmod.glob(
            _config.os.path.join(p, "sessions", "**", "agents", "*", "wire.jsonl"),
            recursive=True,
        ):
            try:
                if since_ts and _config.os.path.getmtime(f) < since_ts:
                    continue
            except OSError:
                continue
            agent = _config.os.path.basename(_config.os.path.dirname(f))
            sess_dir = _config.os.path.dirname(
                _config.os.path.dirname(_config.os.path.dirname(f))
            )
            sess = _config.os.path.basename(sess_dir)
            try:
                state = (
                    _config.json.load(
                        open(_config.os.path.join(sess_dir, "state.json"))
                    )
                    or {}
                )
                sess = state.get("sessionId") or state.get("id") or sess
            except (OSError, ValueError, AttributeError):
                pass
            out.append((f, acct, "kimi", (sess, agent)))
    return out
