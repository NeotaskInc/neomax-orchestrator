"""Fleet-wide interactive process and session activity."""


def ambient_main_details(profile, engine="claude", exclude_cwds=None):
    """Details of MAIN sessions live on this account RIGHT NOW — interactive windows AND the
    fleet orchestrator itself, no matter who launched them, BOTH engines. (live_counts shows
    worker processes; this shows the human/orchestrator-driven sessions.) Claude: a main
    transcript (projects/<proj>/<sess>.jsonl, not under subagents/) written within
    AGENT_ACTIVE_WINDOW_S whose last event isn't a final end_turn. Codex: a rollout written
    within the window whose session isn't closed (NOT requiring mid-turn — a Codex orchestrator
    sits at task_complete BETWEEN its autonomous turns, which used to make it vanish here).
    neomax workers are EXCLUDED by cwd: under STATE_DIR, OR under any running worker's
    worktree in `exclude_cwds`. Worker worktrees can live under the PROJECT (not STATE_DIR),
    and a RUNNING worker hasn't recorded its session id yet, so matching the worktree path is
    the reliable de-dup (the orchestrator runs in the project ROOT, never inside a worktree).
    Each item: {session, cwd, branch, slug, age_s}."""
    from . import account_state as _account_state
    from . import config as _config
    from . import provider_activity as _provider_activity
    from . import session_headers as _session_headers

    if engine == "opencode":
        return _provider_activity.opencode_main_details(profile, exclude_cwds)
    if engine == "kimi":
        return _provider_activity.kimi_main_details(profile, exclude_cwds)
    if engine == "grok":
        return _provider_activity.grok_main_details(profile, exclude_cwds)
    exclude_cwds = tuple((c for c in exclude_cwds or () if c))
    now = _config.time.time()
    out = []
    if engine == "claude":
        pat = _config.os.path.join(profile, "projects", "*", "*.jsonl")
    else:
        pat = _config.os.path.join(profile, "sessions", "**", "rollout-*.jsonl")
    for f in _config.globmod.iglob(pat, recursive=engine != "claude"):
        try:
            if now - _config.os.path.getmtime(f) > _account_state.AGENT_ACTIVE_WINDOW_S:
                continue
            size = _config.os.path.getsize(f)
            with open(f, "rb") as fh:
                head = fh.read(262144).decode("utf-8", "replace")
                fh.seek(max(0, size - 65536))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        active = (
            _session_headers._claude_tail_active(tail)
            if engine == "claude"
            else _session_headers._codex_session_live(tail)
        )
        if not active:
            continue
        meta = (
            _session_headers._claude_head_meta
            if engine == "claude"
            else _session_headers._codex_head_meta
        )(head)
        cwd = meta.get("cwd") or ""
        if (
            cwd.startswith(_config.STATE_DIR)
            or (exclude_cwds and cwd.startswith(exclude_cwds))
            or _config.os.sep + "worktrees" + _config.os.sep in cwd
        ):
            continue
        base = _config.os.path.basename(f)[:-6]
        if engine == "claude":
            sess = base
        else:
            sess = base[-36:] if len(base) >= 36 else base
        out.append(
            {
                "session": sess,
                "cwd": meta.get("cwd"),
                "branch": meta.get("branch"),
                "slug": meta.get("slug"),
                "label": meta.get("label"),
                "age_s": int(now - _config.os.path.getmtime(f)),
            }
        )
    return out


def ambient_active_mains(profile, engine="claude"):
    """Count of active main sessions on this account (see ambient_main_details)."""
    return len(ambient_main_details(profile, engine))


def process_live_counts(engine="claude"):
    """Count real running worker processes per profile via their environment."""
    from . import config as _config
    from . import models as _models

    cfg = _models.ENGINES[engine]
    plist = _models.engine_profiles(engine)
    counts = {p: 0 for p in plist}
    env_key = cfg["config_env"] + "="
    default = cfg["default_dir"]
    try:
        pids = _config.subprocess.run(
            ["pgrep", "-U", str(_config.os.getuid())] + list(cfg["pgrep"]),
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.split()
    except (FileNotFoundError, _config.subprocess.TimeoutExpired):
        return counts
    pidset = set(pids)
    for pid in pids:
        try:
            out = _config.subprocess.run(
                ["ps", "eww", "-o", "ppid=,command=", "-p", pid],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except _config.subprocess.TimeoutExpired:
            continue
        if not out:
            continue
        (ppid, _, cmd) = out.partition(" ")
        if ppid.strip() in pidset:
            continue
        proc_cfg = default
        for tok in reversed(cmd.split()):
            if tok.startswith(env_key):
                val = tok.split("=", 1)[1]
                if val in counts:
                    proc_cfg = val
                    break
        if proc_cfg in counts:
            counts[proc_cfg] += 1
    return counts
