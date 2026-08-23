"""Self-healing and detached worker reconciliation."""

from . import config as _config

SELF_HEAL_FILE = _config.os.path.join(_config.STATE_DIR, "self-heal.json")


def load_self_heal():
    from . import config as _config

    try:
        with open(SELF_HEAL_FILE) as f:
            return _config.json.load(f)
    except (OSError, ValueError):
        return {}


def record_self_heal(runid, action):
    from . import config as _config

    d = load_self_heal()
    e = d.setdefault(runid, {"attempts": 0, "history": []})
    e["attempts"] += 1
    e["history"].append({"ts": int(_config.time.time()), "action": action})
    try:
        _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
        tmp = SELF_HEAL_FILE + ".tmp"
        with open(tmp, "w") as f:
            _config.json.dump(d, f)
        _config.os.rename(tmp, SELF_HEAL_FILE)
    except OSError:
        pass


def dispatch_detached(action, runid):
    """Spawn `neomax <action> <runid>` detached — resume/retry BLOCK for the
    whole worker run, so heal can't call them inline; fire-and-track via the registry."""
    from . import config as _config

    self_path = _config.os.path.abspath(_config.sys.argv[0])
    _config.os.makedirs(_config.LOGS_DIR, exist_ok=True)
    logf = open(_config.os.path.join(_config.LOGS_DIR, "heal-%s.log" % runid), "ab")
    _config.subprocess.Popen(
        [self_path, action, runid],
        stdout=logf,
        stderr=logf,
        stdin=_config.subprocess.DEVNULL,
        start_new_session=True,
    )


def kill_worker_inline(rec):
    from . import config as _config
    from . import gitops as _gitops
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    wp = rec.get("worker_pid")
    if _orchestrators.worker_alive(rec):
        try:
            _config.os.killpg(_config.os.getpgid(wp), _config.signal.SIGTERM)
            _config.time.sleep(2)
            _config.os.killpg(_config.os.getpgid(wp), _config.signal.SIGKILL)
        except OSError:
            pass
    if rec.get("status") == "running":
        rec["status"] = "aborted"
        rec["ended"] = int(_config.time.time())
        rec["acknowledged"] = False
        _gitops.worktree_outcome(rec)
        _runs.save_run(rec)
        _runs.log_event(rec, "killed", by="heal")


def cmd_reconcile(argv):
    """Sweep the durable ledger across BOTH engines and drive everything to a
    terminal state so nothing is ever silently lost. Re-stamps dead 'running' runs
    honestly, surfaces every unresolved run + sub-agent, and prints the next action.
    With --heal it ACTS: auto resume/retry/kill of stale runs (bounded, deduped)."""
    from . import cleanup as _cleanup
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    heal = "--heal" in argv
    allow_repeat = "--allow-repeat" in argv
    max_age = 6.0
    max_heal = 5
    if _config.os.path.isdir(_runs.HISTORY_PENDING):
        for name in sorted(_config.os.listdir(_runs.HISTORY_PENDING)):
            if not name.endswith(".json"):
                continue
            fp = _config.os.path.join(_runs.HISTORY_PENDING, name)
            try:
                rec = _config.json.load(open(fp))
            except (OSError, ValueError):
                continue
            _runs.archive_run(rec)
            if _runs.history_get(rec.get("id")):
                try:
                    _config.os.remove(fp)
                except OSError:
                    pass
                _models.err("neomax reconcile: archived pending run %s" % rec.get("id"))
    _tidied = _cleanup.merged_terminal_runs()
    for _r in _tidied:
        _cleanup._resolve_merged_run(_r)
    if _tidied:
        print(
            "reconcile: auto-resolved %d run(s) already merged into main (inbox cleared of them)."
            % len(_tidied)
        )
    a = list(argv)
    while a:
        x = a.pop(0)
        if x == "--max-age-hours":
            max_age = float(a.pop(0))
        elif x == "--max":
            max_heal = int(a.pop(0))
    runs = _runs.all_runs()
    if not runs:
        print("reconcile: ledger empty — nothing outstanding")
        return
    buckets = {
        "resolved": [],
        "orphaned": [],
        "needs_resume": [],
        "needs_retry": [],
        "has_changes": [],
        "running": [],
    }
    for rec in runs:
        st = _orchestrators.effective_status(rec)
        if rec.get("status") == "running" and st == "interrupted":
            rec["status"] = "interrupted"
            _runs.save_run(rec)
        unresolved_children = [
            c
            for c in rec.get("children") or []
            if c.get("status") not in ("completed", "error")
        ]
        if st == "orphaned":
            buckets["orphaned"].append((rec, unresolved_children))
        elif st == "running":
            buckets["running"].append((rec, unresolved_children))
        elif st in ("stalled", "timeout", "aborted", "interrupted"):
            buckets["needs_resume"].append((rec, unresolved_children))
        elif st in ("error", "limit"):
            buckets["needs_retry"].append((rec, unresolved_children))
        elif rec.get("worktree_state") == "has_changes":
            buckets["has_changes"].append((rec, unresolved_children))
        else:
            buckets["resolved"].append((rec, unresolved_children))

    def show(title, key, action):
        items = buckets[key]
        if not items:
            return
        print("\n%s (%d):" % (title, len(items)))
        for rec, kids in items:
            line = "  %s  [%s/%s]  %s" % (
                rec["id"],
                rec.get("engine", "claude"),
                rec.get("profile", "?").split("/")[-1],
                (rec.get("prompt") or "")[:48].replace("\n", " "),
            )
            if kids:
                line += "  (%d unresolved sub-agent%s)" % (
                    len(kids),
                    "" if len(kids) == 1 else "s",
                )
            print(line)
            print("      → %s" % action.format(id=rec["id"]))

    if heal:
        sh = load_self_heal()
        now = _config.time.time()
        (healed, skipped) = ([], 0)
        for key, action in (
            ("needs_retry", "retry"),
            ("needs_resume", "resume"),
            ("orphaned", "resume"),
        ):
            for rec, _kids in buckets[key]:
                if len(healed) >= max_heal:
                    break
                age_h = (now - (rec.get("ended") or rec.get("started", 0))) / 3600.0
                if max_age and age_h > max_age:
                    skipped += 1
                    continue
                if _orchestrators.worker_alive(rec) and key != "orphaned":
                    continue
                if not allow_repeat and rec["id"] in sh:
                    skipped += 1
                    continue
                if key == "orphaned":
                    kill_worker_inline(rec)
                dispatch_detached(action, rec["id"])
                record_self_heal(rec["id"], action)
                _runs.log_event(rec, "self_heal", action=action)
                healed.append((rec["id"], action))
        print(
            "\nreconcile --heal: dispatched %d (%s)%s"
            % (
                len(healed),
                ", ".join(("%s→%s" % (r, a) for (r, a) in healed)) or "none",
                "; %d skipped (too old / already healed)" % skipped if skipped else "",
            )
        )
        print(
            "           watch progress: neomax ls   (re-run with --allow-repeat to heal again, or --max N to dispatch more)"
        )
        return
    show(
        "ORPHANED — worker still alive after engine died",
        "orphaned",
        "neomax kill {id}   (then resume/retry/clean)",
    )
    show("RUNNING — live now", "running", "in progress; neomax log {id} to watch")
    show(
        "NEEDS RESUME — interrupted/stalled, context kept",
        "needs_resume",
        "neomax resume {id}",
    )
    show("NEEDS RETRY — errored or rate-limited", "needs_retry", "neomax retry {id}")
    show(
        "READY TO MERGE — has committed/uncommitted work",
        "has_changes",
        "review git diff, merge, then: neomax clean {id}",
    )
    n_unresolved = sum((len(buckets[k]) for k in buckets if k != "resolved"))
    print(
        "\nreconcile: %d resolved, %d outstanding.  (auto-resolve: neomax reconcile --heal)"
        % (len(buckets["resolved"]), n_unresolved)
    )
    if buckets["resolved"]:
        print("           tidy finished runs with: neomax clean --done")
