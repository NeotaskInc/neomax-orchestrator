"""Run safety gates, supervision, and pull-request lifecycle."""


def refuse_if_worker_alive(rec, verb):
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators

    if _orchestrators.worker_alive(rec):
        _models.err(
            "neomax: run %s still has a LIVE worker (pid %s) — refusing to %s (would collide). Stop it first: neomax kill %s"
            % (rec["id"], rec.get("worker_pid"), verb, rec["id"])
        )
        _config.sys.exit(3)


def refuse_if_other_orch(rec, verb, argv):
    """If this run belongs to a DIFFERENT live orchestrator session, refuse the destructive
    op unless --any is passed. Protects concurrent orchestrators from acking/cleaning/killing/
    resuming each other's work. No-op for a bare CLI call (no current orch session)."""
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators

    if "--any" in argv:
        return
    if _orchestrators.owned_by_other_live_orch(rec):
        owner = _orchestrators.run_owner(rec)
        info = next(
            (
                o
                for o in _orchestrators.all_orchestrators()
                if o.get("session") == owner
            ),
            {},
        )
        _models.err(
            "neomax: run %s belongs to ANOTHER live orchestrator (%s acct %s, project %s) — refusing to %s it. Coordinate with that session, or override with --any."
            % (
                rec["id"],
                info.get("engine", "?"),
                info.get("account", "?"),
                info.get("project", "?"),
                verb,
            )
        )
        _config.sys.exit(3)


def ensure_workdir(rec):
    """Make rec['workdir'] valid for a (re)run. If the isolated worktree vanished
    (TMPDIR purge etc.), recreate it from the recorded branch — NEVER silently
    fall back to the live checkout."""
    from . import config as _config
    from . import gitops as _gitops
    from . import models as _models

    if _config.os.path.isdir(rec["workdir"]):
        return True
    (repo, branch, base) = (rec.get("repo"), rec.get("branch"), rec.get("base"))
    if repo and branch and _config.os.path.isdir(repo):
        _gitops.git(["worktree", "prune"], repo)
        _config.os.makedirs(_config.WORKTREES_DIR, exist_ok=True)
        wt = _config.os.path.join(
            _config.WORKTREES_DIR, "%s-%s" % (_config.os.path.basename(repo), rec["id"])
        )
        if _gitops.git(["rev-parse", "--verify", branch], repo).returncode == 0:
            r = _gitops.git(["worktree", "add", wt, branch], repo)
            if r.returncode == 0:
                rec["workdir"] = wt
                rec["worktree"] = wt
                _models.err(
                    "neomax: recreated vanished worktree at %s (committed work recovered from branch %s; uncommitted progress was lost)"
                    % (wt, branch)
                )
                return True
            _models.err("neomax: could not rebuild worktree: %s" % r.stderr.strip())
        elif rec.get("worktree_state") in ("cleaned", "empty_kept", None) and base:
            if _gitops.git(["rev-parse", "--verify", base], repo).returncode == 0:
                r = _gitops.git(["worktree", "add", "-b", branch, wt, base], repo)
                if r.returncode == 0:
                    rec["workdir"] = wt
                    rec["worktree"] = wt
                    _models.err(
                        "neomax: started a fresh isolated worktree at %s (prior attempt left no committed work)"
                        % wt
                    )
                    return True
        _models.err(
            "neomax: workdir for run %s is gone and its branch had committed work that is now unrecoverable — NOT falling back to the live checkout (investigate before retrying)."
            % rec["id"]
        )
        return False
    _models.err(
        "neomax: workdir for run %s is gone and its branch is unrecoverable. Not falling back to the live checkout."
        % rec["id"]
    )
    return False


def cmd_pr(argv):
    """Open a PR for a run's branch, or for an arbitrary branch in the cwd repo.
    neomax pr RUNID
    neomax pr --branch NAME [--base REF] [--title T]   (run inside the repo)"""
    from . import config as _config
    from . import gitops as _gitops
    from . import models as _models
    from . import premerge as _premerge
    from . import runs as _runs

    branch = base = title = None
    runid = None
    a = list(argv)
    while a:
        x = a.pop(0)
        if x == "--branch":
            branch = a.pop(0)
        elif x == "--base":
            base = a.pop(0)
        elif x == "--title":
            title = a.pop(0)
        elif not x.startswith("-"):
            runid = x
        else:
            _premerge.usage()
    if runid:
        rec = _runs.load_run(runid)
        if not rec:
            _models.err("neomax: unknown run %s" % runid)
            _config.sys.exit(1)
        _gitops.open_pr(rec, base=base or rec.get("base_ref"))
        return
    if not branch:
        _premerge.usage()
    r = _gitops.git(["rev-parse", "--show-toplevel"], _config.os.getcwd())
    if r.returncode != 0:
        _models.err("neomax: not in a git repo")
        _config.sys.exit(2)
    repo = r.stdout.strip()
    synthetic = {
        "id": "pr-" + branch.replace("/", "-"),
        "repo": repo,
        "branch": branch,
        "prompt": title or branch,
        "result_text": "Integration PR opened by neomax.",
        "profile": "-",
        "engine": "claude",
        "status": "done",
        "acknowledged": True,
        "started": int(_config.time.time()),
        "ended": int(_config.time.time()),
    }
    _gitops.open_pr(synthetic, base=base)


def cmd_kill(argv):
    """Stop a run LOSSLESSLY (pause: kill now, `neomax resume <id>` later).
    If the run's SUPERVISOR is still alive, signal IT — its on_signal kills the
    worker group, marks the run 'aborted', and KEEPS the worktree (resume-ready).
    Signaling only the worker would race the live supervisor: its poll() would
    classify the SIGTERM'd worker as 'error' and worktree_outcome would clean a
    no-changes worktree+branch — destroying the pause. killpg directly is the
    fallback ONLY for a true orphan (supervisor already dead). A sticky
    rec['killed'] additionally pins the worktree against any late writer."""
    from . import config as _config
    from . import gitops as _gitops
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import premerge as _premerge
    from . import runs as _runs

    if not argv:
        _premerge.usage()
    rec = _runs.load_run(argv[0])
    if not rec:
        _models.err("neomax: unknown run %s" % argv[0])
        _config.sys.exit(1)
    refuse_if_other_orch(rec, "kill", argv)
    rec["killed"] = True
    _runs.save_run(rec)
    sup = rec.get("pid")
    if sup and sup != _config.os.getpid() and _runs.pid_alive(sup):
        try:
            _config.os.kill(sup, _config.signal.SIGTERM)
        except OSError:
            pass
        for _ in range(25):
            _config.time.sleep(0.2)
            cur = _runs.load_run(rec["id"]) or rec
            if cur.get("status") != "running":
                _models.err(
                    "neomax: run %s aborted via its supervisor — worktree kept; resume with: neomax resume %s"
                    % (rec["id"], rec["id"])
                )
                return
            if not _runs.pid_alive(sup):
                break
        rec = _runs.load_run(rec["id"]) or rec
    wp = rec.get("worker_pid")
    if _orchestrators.worker_alive(rec):
        try:
            _config.os.killpg(_config.os.getpgid(wp), _config.signal.SIGTERM)
            _config.time.sleep(2)
            _config.os.killpg(_config.os.getpgid(wp), _config.signal.SIGKILL)
        except OSError:
            pass
        _models.err("neomax: killed worker pid %s for run %s" % (wp, rec["id"]))
    else:
        _models.err("neomax: no live worker for run %s" % rec["id"])
    if rec["status"] == "running":
        rec["status"] = "aborted"
        rec["ended"] = int(_config.time.time())
        rec["acknowledged"] = False
        rec["killed"] = True
        _gitops.worktree_outcome(rec)
        _runs.save_run(rec)
        _runs.log_event(rec, "killed")


DELEGATION_BRIEF_MIN = 240


def delegation_brief_check(prompt, brief_ok=False, ultra=False):
    """Soft quality gate on a DELEGATED worker prompt. The orchestrator runs the smartest
    model — precisely so it authors targeted, fully-planned briefs for its workers;
    a vague hand-off wastes a worker's whole run. Warns (never blocks; --brief acknowledges
    a genuinely trivial task) when a prompt looks under-specified: short AND missing the
    marks of a real brief — an absolute path/scope, and an acceptance/verification clause."""
    from . import models as _models

    if brief_ok:
        return
    p = prompt or ""
    has_scope = "/" in p or any(
        (
            k in p.lower()
            for k in ("file", "path", "repo", "function", "component", "module")
        )
    )
    has_accept = any(
        (
            k in p.lower()
            for k in (
                "test",
                "verif",
                "acceptance",
                "pass",
                "prove",
                "criteria",
                "ensure",
                "confirm",
                "gate",
            )
        )
    )
    thin = len(p) < DELEGATION_BRIEF_MIN
    if thin and (not (has_scope and has_accept)):
        _models.err(
            "neomax: ⚠ THIN DELEGATION PROMPT (%d chars). You are the orchestrator (smartest model) — give the worker a COMPLETE brief, not a one-liner. A good brief = OBJECTIVE (exact end state) · CONTEXT/WHY (the worker can't see your chat) · SCOPE (absolute repo path + the files to touch) · GROUND RULES (paste them — workers don't see CLAUDE.md) · APPROACH/PHASES (your plan, so it executes your decomposition) · ACCEPTANCE (tests/real-surface proof + the gate) · DO-NOT-TOUCH (files other workers own). Tip: use your OWN sub-agents / Workflow / a `--plan` scout to research + draft the brief first, then dispatch. (Add --brief to acknowledge a genuinely trivial task.)"
            % len(p)
        )


def detach_supervisor(rec):
    """Fire the worker's blocking supervisor in its OWN session (start_new_session=True) and return
    immediately, so the worker SURVIVES the launching shell.

    Why this exists: `neomax delegate auto` runs the supervisor (`execute`) INLINE. The supervisor installs
    SIGHUP/SIGINT/SIGTERM handlers that kill the worker (so a closed terminal doesn't strand it). But
    an orchestrator launches `auto` from a Bash tool call — when that shell is reaped at turn-end, or
    hits the Bash tool's timeout (~the 4-5-min 'all 8 workers vanished at once'), the supervisor takes
    SIGHUP/SIGTERM and dutifully kills its worker. Detaching the supervisor into a fresh session makes
    it immune to the launcher's lifecycle; it still cleans up its own worker on a genuine signal/exit
    or `neomax kill`. The run is fully tracked — adopt/track it via `neomax ls`/`status`."""
    from . import config as _config
    from . import models as _models
    from . import runs as _runs

    _runs.save_run(rec)
    _config.os.makedirs(_config.LOGS_DIR, exist_ok=True)
    self_path = _config.os.path.abspath(_config.sys.argv[0])
    try:
        slog = open(
            _config.os.path.join(_config.LOGS_DIR, "supervise-%s.log" % rec["id"]), "ab"
        )
    except OSError:
        slog = _config.subprocess.DEVNULL
    try:
        _config.subprocess.Popen(
            [self_path, "__supervise", rec["id"]],
            stdout=slog,
            stderr=slog,
            stdin=_config.subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        if slog is not _config.subprocess.DEVNULL:
            slog.close()
    _models.err(
        "neomax: detached — the worker supervises in its OWN session, surviving this shell/turn (no SIGHUP/timeout reaping). Track it: neomax ls / neomax status."
    )


def cmd_supervise(argv):
    """INTERNAL (spawned by `auto`'s detach): run the blocking supervisor for an already-set-up run
    in this fresh, detached session. Not for interactive use."""
    from . import config as _config
    from . import failover as _failover
    from . import models as _models
    from . import runs as _runs

    if not argv:
        _config.sys.exit(2)
    rec = _runs.load_run(argv[0])
    if not rec:
        _models.err("neomax: __supervise: unknown run %s" % argv[0])
        _config.sys.exit(1)
    _failover.run_with_failover(rec)
