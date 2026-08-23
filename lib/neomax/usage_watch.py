"""Usage-ledger watch pass and state."""


def usage_watch_pass(state, baseline=False, full=False):
    """One incremental sweep over active sessions: read each file from its saved byte offset
    to EOF, emit usage records for new completions (stamped with each completion's REAL
    timestamp), advance the offset. Steady-state only looks at sessions touched in the last
    USAGE_RECENT_DAYS (cheap); `full=True` scans ALL history (the initial backfill / rebuild,
    so 7d/30d/all-time are populated with correctly-dated records). `baseline=True` only seeds
    offsets to current EOF WITHOUT parsing history. Idempotent: aggregation dedups by id, so a
    re-read (compaction/truncation) can't double-count."""
    from . import config as _config
    from . import usage_ingest as _usage_ingest

    files = state.setdefault("files", {})
    ctot = state.setdefault("codex_total", {})
    cmods = state.setdefault("codex_model", {})
    recent = (
        0 if full else _config.time.time() - _usage_ingest.USAGE_RECENT_DAYS * 86400
    )
    records = []
    total = 0

    def emit(line):
        if not line.strip():
            return
        try:
            if provider == "claude":
                r = _usage_ingest._claude_records_from_line(line, acct)
                if r:
                    records.append(r)
            elif provider == "kimi":
                r = _usage_ingest._kimi_record_from_line(line, acct, sess[0], sess[1])
                if r:
                    records.append(r)
            else:
                seen = _usage_ingest.codex_model_in_line(line)
                if seen:
                    cmods[sess] = seen
                r = _usage_ingest._codex_record_from_line(
                    line, acct, sess, cmods.get(sess)
                )
                if r:
                    tot = r["in"] + r["cr"] + r["out"]
                    if tot > ctot.get(sess, 0):
                        ctot[sess] = tot
                        records.append(r)
        except (ValueError, TypeError, KeyError):
            return

    for path, acct, provider, sess in _usage_ingest._usage_session_files(
        since_ts=recent
    ):
        try:
            size = _config.os.path.getsize(path)
        except OSError:
            continue
        if baseline:
            files[path] = size
            continue
        off = files.get(path, 0)
        if size < off:
            off = 0
        if size == off:
            continue
        if full:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        emit(line.rstrip("\n"))
            except OSError:
                continue
            files[path] = size
            if len(records) >= 20000:
                _usage_ingest._ledger_append(records)
                total += len(records)
                records = []
            continue
        try:
            with open(path, "rb") as fh:
                fh.seek(off)
                chunk = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        files[path] = size
        lines = chunk.split("\n")
        if not chunk.endswith("\n"):
            files[path] = size - len(lines[-1].encode("utf-8", "replace"))
            lines = lines[:-1]
        for line in lines:
            emit(line)
    _usage_ingest._ledger_append(records)
    total += len(records)
    return (state, total)


def _save_watch_state(state):
    from . import config as _config

    try:
        _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
        tmp = _config.USAGE_WATCH_STATE + ".tmp"
        with open(tmp, "w") as f:
            _config.json.dump(state, f)
        _config.os.rename(tmp, _config.USAGE_WATCH_STATE)
    except OSError:
        pass


KEEPALIVE_MARGIN_S = 45 * 60
KEEPALIVE_GAP_S = 15 * 60
KEEPALIVE_EVERY_S = 8 * 60
