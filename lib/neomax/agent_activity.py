"""Native agent activity discovery."""


def ambient_agent_details(profile, engine="claude"):
    """Details of every sub-agent ACTIVE on this account right now, from their transcripts
    on disk — regardless of who spawned them (an interactive session's Agent-tool fleet, a
    workflow, or a neomax worker). Sub-agents aren't separate processes (they run
    in-process), so pgrep can't see them — but each one appends to its own
    projects/<proj>/<session>/subagents/**/agent-*.jsonl as it works. Active = file written
    within AGENT_ACTIVE_WINDOW_S AND its last event isn't a final end_turn assistant turn.
    ADDITIONALLY, every agent of an IN-PROGRESS workflow (its journal.jsonl written within
    WORKFLOW_RECENT_S) is included even after it finishes its turn, so a running workflow's WHOLE
    fleet stays visible — each carries `working` (mid-turn now) / `done` (finished its turn).
    Each item: {session, slug, cwd, branch, label, age_s, wf, working, done, tok, worker} —
    worker = spawned by a neomax worker (cwd under our worktrees) vs the user's own session.
    Codex sub-agents have no per-agent transcript → [] here (covered by worker streams)."""
    from . import account_state as _account_state
    from . import config as _config
    from . import provider_activity as _provider_activity

    if engine == "opencode":
        return _provider_activity.opencode_agent_details(profile)
    if engine == "kimi":
        return _provider_activity.kimi_agent_details(profile)
    if engine == "grok":
        return _provider_activity.grok_agent_details(profile)
    if engine != "claude":
        return []
    now = _config.time.time()
    out = []
    wf_recent = {}

    def _wf_of(path):
        mm = _config.re.search("(.*/subagents/workflows/(wf_[^/]+))/", path)
        if not mm:
            return (None, False)
        (d, wfid) = (mm.group(1), mm.group(2))
        if d not in wf_recent:
            try:
                wf_recent[d] = (
                    now
                    - _config.os.path.getmtime(_config.os.path.join(d, "journal.jsonl"))
                    <= _account_state.WORKFLOW_RECENT_S
                )
            except OSError:
                wf_recent[d] = False
        return (wfid, wf_recent[d])

    for f in _config.globmod.iglob(
        _config.os.path.join(profile, "projects", "**", "subagents", "**", "*.jsonl"),
        recursive=True,
    ):
        if _config.os.path.basename(f) == "journal.jsonl":
            continue
        try:
            age = now - _config.os.path.getmtime(f)
        except OSError:
            continue
        (wf, wf_active) = _wf_of(f)
        if age > _account_state.AGENT_ACTIVE_WINDOW_S and (not wf_active):
            continue
        try:
            size = _config.os.path.getsize(f)
            with open(f, "rb") as fh:
                head = fh.read(8192).decode("utf-8", "replace")
                fh.seek(max(0, size - 4096))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        last = None
        for line in tail.strip().split("\n"):
            try:
                last = _config.json.loads(line)
            except ValueError:
                pass
        msg = (last or {}).get("message") or {}
        done = (last or {}).get("type") == "assistant" and msg.get(
            "stop_reason"
        ) == "end_turn"
        if done and (not wf_active):
            continue
        working = age <= _account_state.AGENT_ACTIVE_WINDOW_S and (not done)
        first = {}
        try:
            first = _config.json.loads(head.split("\n", 1)[0])
        except ValueError:
            pass
        c = (first.get("message") or {}).get("content")
        if isinstance(c, list):
            c = next(
                (b.get("text") for b in c if isinstance(b, dict) and b.get("text")), ""
            )
        label = " ".join(str(c or "").split())[:90]
        cwd = first.get("cwd") or ""
        worker = cwd.startswith(_config.STATE_DIR)
        tin = tout = tcache = 0
        if not worker:
            try:
                with open(f, "rb") as fh:
                    for raw in fh:
                        if b'"usage"' not in raw or b'"assistant"' not in raw:
                            continue
                        try:
                            uu = (_config.json.loads(raw).get("message") or {}).get(
                                "usage"
                            ) or {}
                        except (ValueError, AttributeError):
                            continue
                        tin += uu.get("input_tokens") or 0
                        tout += uu.get("output_tokens") or 0
                        tcache += (uu.get("cache_creation_input_tokens") or 0) + (
                            uu.get("cache_read_input_tokens") or 0
                        )
            except OSError:
                pass
        out.append(
            {
                "session": first.get("sessionId")
                or _config.os.path.basename(
                    _config.os.path.dirname(_config.os.path.dirname(f))
                ),
                "slug": first.get("slug"),
                "cwd": cwd,
                "branch": first.get("gitBranch"),
                "label": label,
                "age_s": int(age),
                "wf": wf,
                "done": done,
                "working": working,
                "tok": {"in": tin, "out": tout, "cache": tcache},
                "worker": worker,
            }
        )
    return out


def ambient_live_agents(profile, engine="claude"):
    """Count of active sub-agents on this account (see ambient_agent_details)."""
    return len(ambient_agent_details(profile, engine))


CLAUDE_META_TYPES = (
    "summary",
    "bridge-session",
    "permission-mode",
    "mode",
    "ai-title",
    "file-history-snapshot",
    "queued-command",
    "system",
)
