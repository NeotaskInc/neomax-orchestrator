"""Global agent-budget admission queue."""

from . import config as _config

AGENT_QUEUE_FILE = _config.os.path.join(_config.STATE_DIR, "agent-queue.json")
AGENT_BUDGET_DEFAULT = int(_config.os.environ.get("NEOMAX_AGENT_BUDGET", "50"))
TASK_BUDGET_DEFAULT = int(_config.os.environ.get("NEOMAX_TASK_BUDGET", "0"))
QUEUE_RES_TTL_S = float(_config.os.environ.get("NEOMAX_QUEUE_TTL", str(12 * 3600)))


def load_queue():
    from . import config as _config

    try:
        with open(AGENT_QUEUE_FILE) as f:
            d = _config.json.load(f)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("agent_budget", AGENT_BUDGET_DEFAULT)
    d.setdefault("task_budget", TASK_BUDGET_DEFAULT)
    if not isinstance(d.get("queue"), list):
        d["queue"] = []
    return d


def _session_known_dead(session):
    """True only if `session` is REGISTERED in the orchestrator registry AND not live (the genuine
    crash case). An unregistered/unknown session is NOT 'known dead' — we keep its reservation until
    the TTL or an explicit release, so a separate-process CLI caller (whose own pid is already gone)
    never has its reservation reaped out from under it."""
    from . import orchestrators as _orchestrators

    if not session:
        return False
    for o in _orchestrators.all_orchestrators():
        if o.get("session") == session:
            return not o.get("live")
    return False


def _res_alive(r):
    """A reservation is reaped only when its owning orchestrator session is KNOWN-DEAD (crashed) or
    it has aged past the TTL — so the budget self-heals after a crash without ever dropping a live
    task's slots. A transient `pid-` CLI owner is kept until the TTL / explicit release."""
    from . import config as _config

    if not isinstance(r, dict):
        return False
    if _config.time.time() - r.get("ts", 0) > QUEUE_RES_TTL_S:
        return False
    s = r.get("session") or ""
    if s.startswith("pid-"):
        return True
    return not _session_known_dead(s)


def allocate(state):
    """Monotonic FIFO allocator. Grants free agent slots to reservations in queue order; a
    reservation's `granted` only ever GROWS toward its `want` until it's released. Already-active
    reservations top up FIRST (FIFO), then waiting ones START (FIFO) subject to the concurrent-task
    cap. So the front of the line is always filled before anyone behind it gets agents."""
    budget = max(0, int(state.get("agent_budget", AGENT_BUDGET_DEFAULT)))
    task_budget = int(state.get("task_budget", 0) or 0)
    q = state["queue"]

    def used():
        return sum((min(int(r.get("granted", 0)), int(r.get("want", 0))) for r in q))

    free = max(0, budget - used())
    for r in q:
        if free <= 0:
            break
        (g, w) = (int(r.get("granted", 0)), int(r.get("want", 0)))
        if 0 < g < w:
            add = min(w - g, free)
            r["granted"] = g + add
            free -= add
    active = sum((1 for r in q if int(r.get("granted", 0)) > 0))
    free = max(0, budget - used())
    for r in q:
        if free <= 0:
            break
        (g, w) = (int(r.get("granted", 0)), int(r.get("want", 0)))
        if g == 0 and w > 0:
            if task_budget and active >= task_budget:
                break
            add = min(w, free)
            r["granted"] = add
            free -= add
            if add > 0:
                active += 1
    return state


def _queue_rmw(fn):
    """flock-guarded: reap dead reservations → run fn(state) → re-allocate → persist. fn's return
    value is passed back. Never raises."""
    from . import config as _config

    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(AGENT_QUEUE_FILE + ".lock", "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        state = load_queue()
        state["queue"] = [r for r in state["queue"] if _res_alive(r)]
        result = fn(state)
        allocate(state)
        tmp = AGENT_QUEUE_FILE + ".tmp.%d" % _config.os.getpid()
        with open(tmp, "w") as f:
            _config.json.dump(state, f, indent=2)
        _config.os.replace(tmp, AGENT_QUEUE_FILE)
        return result
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass


def queue_reserve(task, agents, session=None, batch=None):
    """Reserve `agents` slots for `task` (FIFO; a `batch` tag groups issues from the same find pass —
    they reserve consecutively, so the order is FIFO-BY-BATCH and a batch stays contiguous in line).
    If the task already holds a reservation its want is raised (top-up), keeping its place. Returns
    the reservation dict with the slots GRANTED now (0..want) after allocation."""
    from . import config as _config
    from . import orchestrators as _orchestrators

    sess = (
        session
        or _orchestrators.current_orch_session()
        or "pid-%d" % _config.os.getpid()
    )

    def fn(state):
        for r in state["queue"]:
            if r.get("task") == task:
                r["want"] = max(int(r.get("want", 0)), int(agents))
                if batch:
                    r["batch"] = batch
                return r
        r = {
            "id": "res-"
            + _config.time.strftime("%Y%m%d-%H%M%S")
            + "-"
            + _config.os.urandom(2).hex(),
            "task": task,
            "want": max(0, int(agents)),
            "granted": 0,
            "batch": batch,
            "ts": _config.time.time(),
            "session": sess,
        }
        state["queue"].append(r)
        return r

    return _queue_rmw(fn)


def queue_poll(resid=None, task=None):

    def fn(state):
        for r in state["queue"]:
            if resid and r.get("id") == resid or (task and r.get("task") == task):
                return r
        return None

    return _queue_rmw(fn)


def queue_release(resid=None, task=None):

    def fn(state):
        before = len(state["queue"])
        state["queue"] = [
            r
            for r in state["queue"]
            if not (resid and r.get("id") == resid or (task and r.get("task") == task))
        ]
        return before - len(state["queue"])

    return _queue_rmw(fn)


def queue_set(agent_budget=None, task_budget=None):

    def fn(state):
        if agent_budget is not None:
            state["agent_budget"] = max(0, int(agent_budget))
        if task_budget is not None:
            state["task_budget"] = max(0, int(task_budget))
        return (state["agent_budget"], state["task_budget"])

    return _queue_rmw(fn)


def queue_snapshot():
    """The whole queue state after a reap + re-allocate (what `queue status` renders).
    _queue_rmw reaps + allocates + persists, so reading the file afterwards yields the
    POST-allocate state (a reap that frees slots to a waiter is reflected, not stale)."""
    _queue_rmw(lambda s: None)
    return load_queue()


def cmd_queue(argv):
    """Global agent-budget queue — the concurrency governor (FIFO admission control).
    neomax queue status [--json]
    neomax queue reserve --task T --agents N [--batch B] [--json]   (reserve slots; FIFO-by-batch; returns granted now)
    neomax queue poll (--id R | --task T) [--json]      (re-check how many slots are granted)
    neomax queue release (--id R | --task T)            (free a task's slots when it's done)
    neomax queue set-budget [--agents N] [--tasks N]    (tune the caps; start 50 agents)"""
    from . import config as _config
    from . import issue_commands as _issue_commands
    from . import models as _models

    if not argv:
        _models.err("usage: neomax queue <status|reserve|poll|release|set-budget> ...")
        _config.sys.exit(2)
    (sub, rest) = (argv[0], argv[1:])
    (opts, _) = _issue_commands._issue_argparse(rest)
    if sub == "status":
        s = queue_snapshot()
        used = sum(
            (min(int(r.get("granted", 0)), int(r.get("want", 0))) for r in s["queue"])
        )
        if opts.get("--json"):
            print(
                _config.json.dumps(
                    {
                        "agent_budget": s["agent_budget"],
                        "task_budget": s["task_budget"],
                        "used": used,
                        "free": max(0, s["agent_budget"] - used),
                        "active_tasks": sum(
                            (1 for r in s["queue"] if int(r.get("granted", 0)) > 0)
                        ),
                        "queued_tasks": len(s["queue"]),
                        "queue": s["queue"],
                    },
                    indent=2,
                )
            )
            return
        print(
            "agent budget %d | used %d | free %d | tasks %d (cap %s)"
            % (
                s["agent_budget"],
                used,
                max(0, s["agent_budget"] - used),
                len(s["queue"]),
                s["task_budget"] or "∞",
            )
        )
        for r in s["queue"]:
            wait = max(0, int(r.get("want", 0)) - int(r.get("granted", 0)))
            bt = " [%s]" % r.get("batch") if r.get("batch") else ""
            print(
                "  %-26s want %3d  granted %3d  %s%s"
                % (
                    r.get("task"),
                    r.get("want", 0),
                    r.get("granted", 0),
                    "WAITING on %d" % wait if wait else "running",
                    bt,
                )
            )
        return
    if sub == "reserve":
        task = opts.get("--task")
        if not task or not opts.get("--agents"):
            _models.err("queue reserve: --task and --agents required")
            _config.sys.exit(2)
        try:
            n = int(opts["--agents"])
        except (TypeError, ValueError):
            _models.err("queue reserve: --agents must be an integer")
            _config.sys.exit(2)
        r = queue_reserve(task, n, batch=opts.get("--batch"))
        wait = max(0, int(r["want"]) - int(r["granted"]))
        if opts.get("--json"):
            print(
                _config.json.dumps(
                    {
                        "id": r["id"],
                        "task": r["task"],
                        "want": r["want"],
                        "granted": r["granted"],
                        "waiting": wait,
                    },
                    indent=2,
                )
            )
        else:
            _models.err(
                "queue: %s reserved %d, GRANTED %d%s"
                % (
                    task,
                    r["want"],
                    r["granted"],
                    " (waiting on %d)" % wait if wait else "",
                )
            )
            print(r["granted"])
        return
    if sub == "poll":
        r = queue_poll(resid=opts.get("--id"), task=opts.get("--task"))
        if not r:
            print("null" if opts.get("--json") else "(no such reservation)")
            return
        wait = max(0, int(r["want"]) - int(r["granted"]))
        if opts.get("--json"):
            print(
                _config.json.dumps(
                    {
                        "id": r["id"],
                        "task": r["task"],
                        "want": r["want"],
                        "granted": r["granted"],
                        "waiting": wait,
                    },
                    indent=2,
                )
            )
        else:
            print(r["granted"])
        return
    if sub == "release":
        n = queue_release(resid=opts.get("--id"), task=opts.get("--task"))
        print("released %d reservation(s)" % n)
        return
    if sub == "set-budget":
        try:
            ab = int(opts["--agents"]) if opts.get("--agents") else None
            tb = int(opts["--tasks"]) if opts.get("--tasks") else None
        except (TypeError, ValueError):
            _models.err("queue set-budget: --agents/--tasks must be integers")
            _config.sys.exit(2)
        (a, t) = queue_set(agent_budget=ab, task_budget=tb)
        print("agent_budget=%d task_budget=%s" % (a, t or "unlimited"))
        return
    _models.err("queue: unknown subcommand %r" % sub)
    _config.sys.exit(2)
