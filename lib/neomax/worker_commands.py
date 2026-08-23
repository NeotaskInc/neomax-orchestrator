"""Provider worker command construction and execution."""

from . import config as _config


def claude_args(rec, resume_session=None):
    from . import config as _config
    from . import models as _models

    perm = (
        ["--permission-mode", "plan"]
        if rec.get("plan_mode")
        else ["--dangerously-skip-permissions"]
    )
    args = (
        [_config.CLAUDE_BIN, "-p"]
        + perm
        + [
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--append-system-prompt",
            _models.WORKER_DIRECTIVE,
        ]
    )
    if rec.get("ultra"):
        args += ["--settings", _config.json.dumps({"ultracode": True})]
    args += ["--model", rec.get("model") or _config.CLAUDE_DEFAULT_MODEL_1M]
    if rec.get("max_turns"):
        args += ["--max-turns", str(rec["max_turns"])]
    if rec.get("effort"):
        args += ["--effort", rec["effort"]]
    if resume_session:
        args += ["--resume", resume_session]
    elif rec.get("session"):
        args += ["--session-id", rec["session"]]
    p = rec["_prompt_to_send"]
    if rec.get("goal"):
        p = "/goal %s\n\n%s" % (rec["goal"], p)
    args.append(p)
    return args


def codex_args(rec, resume_session=None):
    from . import config as _config
    from . import models as _models

    sandbox = (
        ["-s", "read-only"]
        if rec.get("plan_mode")
        else ["--dangerously-bypass-approvals-and-sandbox"]
    )
    args = (
        [_config.CODEX_BIN, "exec", "--json"]
        + sandbox
        + [
            "-m",
            rec.get("model") or _config.CODEX_MODEL,
            "-c",
            "model_reasoning_effort=%s" % (rec.get("effort") or "high"),
            "-c",
            "service_tier=%s" % _models.CODEX_SERVICE_TIER,
            "-C",
            rec["workdir"],
            "--skip-git-repo-check",
        ]
    )
    p = rec["_prompt_to_send"]
    parts = [_models.WORKER_DIRECTIVE]
    if rec.get("goal"):
        parts.append(_models.codex_goal_block(rec["goal"], rec.get("max_turns")))
    parts.append(p)
    args.append("\n\n".join(parts))
    return args


def opencode_args(rec, resume_session=None):
    from . import config as _config
    from . import models as _models

    args = [
        _config.OPENCODE_BIN,
        "run",
        "--model",
        rec.get("model") or _models.default_model("opencode"),
        "--format",
        "json",
        "--dir",
        rec["workdir"],
        "--agent",
        "plan" if rec.get("plan_mode") else "build",
    ]
    if not rec.get("plan_mode"):
        args.append("--auto")
    if resume_session:
        args += ["--session", resume_session]
    parts = [_models.WORKER_DIRECTIVE]
    if rec.get("goal"):
        parts.append(_models.codex_goal_block(rec["goal"], rec.get("max_turns")))
    parts.append(rec["_prompt_to_send"])
    args.append("\n\n".join(parts))
    return args


def kimi_args(rec, resume_session=None):
    from . import config as _config
    from . import models as _models

    args = [
        _config.KIMI_BIN,
        "-m",
        rec.get("model") or _models.KIMI_MODEL,
        "--output-format",
        "stream-json",
    ]
    if resume_session:
        args += ["-S", resume_session]
    parts = [_models.WORKER_DIRECTIVE]
    if rec.get("goal"):
        parts.append(_models.codex_goal_block(rec["goal"], rec.get("max_turns")))
    if rec.get("plan_mode"):
        parts.append(
            "READ-ONLY PLAN SCOUT: inspect and report a plan only. The runtime exposes read-only tools; do not attempt edits, writes, shell commands, or external mutations."
        )
    parts.append(rec["_prompt_to_send"])
    args += ["--prompt", "\n\n".join(parts)]
    return args


def kimi_plan_home(rec):
    from . import config as _config

    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    root = _config.tempfile.mkdtemp(prefix="kimi-plan-", dir=_config.STATE_DIR)
    _config.os.chmod(root, 448)
    profile = _config.os.path.abspath(rec["profile"])
    for name in ("credentials", "sessions", "session_index.jsonl"):
        source = _config.os.path.join(profile, name)
        if _config.os.path.exists(source):
            _config.os.symlink(
                source,
                _config.os.path.join(root, name),
                target_is_directory=_config.os.path.isdir(source),
            )
    allowed = [
        "Read",
        "Grep",
        "Glob",
        "ReadMediaFile",
        "WebSearch",
        "FetchURL",
        "Agent",
        "AgentSwarm",
        "TodoList",
        "TaskList",
        "TaskOutput",
        "WaitFor",
    ]
    try:
        with open(_config.os.path.join(profile, "config.toml")) as f:
            config = f.read()
    except OSError:
        config = ""
    tools = "[tools]\nenabled = %s\n" % _config.json.dumps(allowed)
    pattern = _config.re.compile("(?ms)^\\[tools\\]\\s*\\n.*?(?=^\\[|\\Z)")
    config = (
        pattern.sub(tools + "\n", config)
        if pattern.search(config)
        else config.rstrip() + "\n\n" + tools
    )
    config_path = _config.os.path.join(root, "config.toml")
    with open(config_path, "w") as f:
        f.write(config.lstrip())
    _config.os.chmod(config_path, 384)
    return root


def grok_args(rec, resume_session=None):
    from . import config as _config
    from . import models as _models

    args = [
        _config.GROK_BIN,
        "--no-auto-update",
        "--output-format",
        "streaming-json",
        "--cwd",
        rec["workdir"],
        "--model",
        rec.get("model") or _models.default_model("grok"),
    ]
    if rec.get("plan_mode"):
        args += ["--permission-mode", "plan"]
    else:
        args.append("--always-approve")
    if resume_session:
        args += ["--resume", resume_session]
    if rec.get("max_turns"):
        args += ["--max-turns", str(rec["max_turns"])]
    parts = [_models.WORKER_DIRECTIVE]
    if rec.get("goal"):
        parts.append(_models.codex_goal_block(rec["goal"], rec.get("max_turns")))
    parts.append(rec["_prompt_to_send"])
    args += ["--single", "\n\n".join(parts)]
    return args


def worker_argv(rec, resume_session=None):
    if rec.get("engine") == "codex":
        return codex_args(rec)
    if rec.get("engine") == "opencode":
        return opencode_args(rec, resume_session=resume_session)
    if rec.get("engine") == "kimi":
        return kimi_args(rec, resume_session=resume_session)
    if rec.get("engine") == "grok":
        return grok_args(rec, resume_session=resume_session)
    return claude_args(rec, resume_session=resume_session)


def execute(rec, resume_session=None):
    """Run one worker attempt; returns status string. Updates rec in place."""
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import event_parsers as _event_parsers
    from . import gitops as _gitops
    from . import models as _models
    from . import runs as _runs
    from . import usage_records as _usage_records

    engine = rec.get("engine", "claude")
    cfg = _models.ENGINES[engine]
    log_file = _config.os.path.join(
        _config.LOGS_DIR, "%s.attempt%d.jsonl" % (rec["id"], rec["attempt"])
    )
    rec["log"] = log_file
    env = dict(_config.os.environ)
    env["NEOMAX_WORKER"] = "1"
    for key in cfg["scrub"]:
        env.pop(key, None)
    for key in (
        "NEOMAX_PROFILES",
        "NEOMAX_CODEX_PROFILES",
        "NEOMAX_OPENCODE_PROFILES",
        "NEOMAX_KIMI_PROFILES",
        "NEOMAX_GROK_PROFILES",
        "NEOMAX_CLAUDE_BIN",
        "NEOMAX_CODEX_BIN",
        "NEOMAX_OPENCODE_BIN",
        "NEOMAX_KIMI_BIN",
        "NEOMAX_GROK_BIN",
        "NEOMAX_NO_WORKTREE",
    ):
        env.pop(key, None)
    kimi_plan_dir = None
    if engine == "codex":
        env["CODEX_HOME"] = _config.os.path.abspath(rec["profile"])
        env["NO_COLOR"] = "1"
        env["GIT_OPTIONAL_LOCKS"] = "0"
    elif engine == "opencode":
        if rec["profile"] == cfg["default_dir"] and cfg["default_unset_env"]:
            env.pop("XDG_DATA_HOME", None)
        else:
            env["XDG_DATA_HOME"] = _config.os.path.abspath(rec["profile"])
        env["OPENCODE_CONFIG_CONTENT"] = _models.opencode_policy_content(
            rec.get("model")
        )
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        env["OPENCODE_DISABLE_SHARE"] = "1"
        env["NO_COLOR"] = "1"
        env["GIT_OPTIONAL_LOCKS"] = "0"
    elif engine == "kimi":
        if rec.get("plan_mode"):
            kimi_plan_dir = kimi_plan_home(rec)
            env["KIMI_CODE_HOME"] = kimi_plan_dir
        elif rec["profile"] == cfg["default_dir"] and cfg["default_unset_env"]:
            env.pop("KIMI_CODE_HOME", None)
        else:
            env["KIMI_CODE_HOME"] = _config.os.path.abspath(rec["profile"])
        env["NO_COLOR"] = "1"
        env["GIT_OPTIONAL_LOCKS"] = "0"
    elif engine == "grok":
        if rec["profile"] == cfg["default_dir"] and cfg["default_unset_env"]:
            env.pop("GROK_HOME", None)
        else:
            env["GROK_HOME"] = _config.os.path.abspath(rec["profile"])
        env["NO_COLOR"] = "1"
        env["GIT_OPTIONAL_LOCKS"] = "0"
    elif rec["profile"] == cfg["default_dir"] and cfg["default_unset_env"]:
        env.pop(cfg["config_env"], None)
    else:
        env[cfg["config_env"]] = rec["profile"]
    wall_s = rec.get("wall_min", 240) * 60
    stall_s = rec.get("stall_min", 30) * 60
    start = _config.time.time()
    logf = open(log_file, "ab")
    errf = open(log_file + ".stderr", "ab")
    try:
        proc = _config.subprocess.Popen(
            worker_argv(rec, resume_session),
            cwd=rec["workdir"],
            env=env,
            stdout=logf,
            stderr=errf,
            stdin=_config.subprocess.DEVNULL,
            start_new_session=True,
        )
    except BaseException:
        logf.close()
        errf.close()
        if kimi_plan_dir:
            _config.shutil.rmtree(kimi_plan_dir, ignore_errors=True)
        raise
    rec["worker_pid"] = proc.pid
    _runs.save_run(rec)

    def kill_worker():
        try:
            _config.os.killpg(proc.pid, _config.signal.SIGTERM)
            _config.time.sleep(2)
            _config.os.killpg(proc.pid, _config.signal.SIGKILL)
        except OSError:
            pass

    def on_signal(signum, _frame):
        kill_worker()
        rec["status"] = "aborted"
        rec["ended"] = int(_config.time.time())
        rec["acknowledged"] = False
        _gitops.worktree_outcome(rec)
        _runs.save_run(rec)
        _runs.log_event(rec, "aborted", signal=signum)
        _models.err(
            "neomax: aborted by signal %d — resume with: neomax resume %s"
            % (signum, rec["id"])
        )
        _config.sys.exit(130)

    _config.signal.signal(_config.signal.SIGINT, on_signal)
    _config.signal.signal(_config.signal.SIGTERM, on_signal)
    _config.signal.signal(_config.signal.SIGHUP, on_signal)
    killed_for = None
    last_size = -1
    last_activity = _config.time.time()
    try:
        while proc.poll() is None:
            _config.time.sleep(2)
            try:
                size = _config.os.path.getsize(log_file)
            except OSError:
                size = 0
            if size != last_size:
                last_size = size
                last_activity = _config.time.time()
            now = _config.time.time()
            if wall_s and now - start > wall_s:
                killed_for = "timeout"
                kill_worker()
                break
            if stall_s and now - last_activity > stall_s:
                killed_for = "stalled"
                kill_worker()
                break
        proc.wait()
    except BaseException:
        kill_worker()
        raise
    finally:
        logf.close()
        errf.close()
        try:
            _config.os.killpg(proc.pid, _config.signal.SIGTERM)
        except OSError:
            pass
        else:
            _config.time.sleep(1)
            try:
                _config.os.killpg(proc.pid, _config.signal.SIGKILL)
            except OSError:
                pass
        if kimi_plan_dir:
            _config.shutil.rmtree(kimi_plan_dir, ignore_errors=True)
    ev = _event_parsers.parse_events(log_file, engine)
    rec["result_text"] = ev["result_text"]
    if ev.get("usage"):
        rec["usage"] = ev["usage"]
    if ev.get("resets_at"):
        rec["resets_at"] = ev["resets_at"]
    if ev.get("limit_window"):
        rec["limit_window"] = ev["limit_window"]
    if ev.get("children"):
        prior = rec.get("children") or []
        prior = [c for c in prior if c.get("attempt") != rec["attempt"]]
        rec["children"] = prior + [
            dict(c, attempt=rec["attempt"]) for c in ev["children"]
        ]
    if ev.get("session_id") and (
        engine in ("codex", "opencode", "kimi", "grok")
        or (not rec.get("session") and (not resume_session))
    ):
        if engine in ("codex", "opencode", "kimi", "grok") and (not resume_session):
            rec["session"] = ev["session_id"]
            _models.err("NEOMAX SESSION id=%s" % rec["session"])
        elif engine == "claude" and (not rec.get("session")):
            rec["session"] = ev["session_id"]
    _usage_records.record_usage(rec, engine)
    if engine == "codex" and ev["rate_limited"] and (not rec.get("resets_at")):
        w = _codex_usage.read_codex_window(rec["profile"], rec.get("session"))
        if w:
            (five, seven) = (w.get("five_hour") or {}, w.get("seven_day") or {})
            (fp, sp) = (five.get("used_percent") or 0, seven.get("used_percent") or 0)
            if sp >= 99 and sp >= fp and seven.get("resets_at"):
                rec["resets_at"] = seven["resets_at"]
                rec["limit_window"] = "weekly"
            elif five.get("resets_at"):
                rec["resets_at"] = five["resets_at"]
                rec["limit_window"] = rec.get("limit_window") or "5h"
    if killed_for:
        return killed_for
    if ev["rate_limited"]:
        return "limit"
    stderr_tail = _codex_usage.tail_file(log_file + ".stderr", 4000)
    blob = (
        (ev["result_text"] or "")
        + "\n"
        + stderr_tail
        + "\n"
        + " ".join(ev.get("errors") or [])
    )
    if (ev["is_error"] or proc.returncode != 0) and _models.INTERRUPT_RE.search(blob):
        return "aborted"
    if (ev["is_error"] or proc.returncode != 0) and _models.LIMIT_RE.search(blob):
        return "limit"
    if (
        proc.returncode != 0
        or ev["is_error"]
        or ev.get("subtype") not in (None, "success")
    ):
        rec["error_detail"] = "%s rc=%s %s" % (
            ev.get("subtype") or "",
            proc.returncode,
            stderr_tail[-400:],
        )
        return "error"
    return "done"


CODEX_AUTH_DEAD = _config.re.compile(
    "\\b401\\b|\\b403\\b|unauthorized|forbidden|not logged in|login required|invalid api key",
    _config.re.I,
)
CODEX_LIMIT = _config.re.compile(
    "rate limit reached|usage limit|tokens per min|\\bTPM\\b|requests per min|\\b429\\b|quota|credits? (?:depleted|exhausted)|please try again in \\d+(?:ms|s)",
    _config.re.I,
)
