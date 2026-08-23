"""Durable project backlog and task commands."""

from . import config as _config

TASKS_FILE = _config.os.path.join(_config.STATE_DIR, "tasks.json")
TASK_STATUSES = ("todo", "doing", "blocked", "done", "merged", "dropped")
TASK_OPEN_STATUSES = ("todo", "doing", "blocked")
TASK_DONE_STATUSES = ("done", "merged", "dropped")


def load_tasks():
    """The task registry: {"seq": N, "tasks": {id: {...}}}. Never raises."""
    from . import config as _config

    try:
        with open(TASKS_FILE) as f:
            d = _config.json.load(f)
        if isinstance(d, dict):
            d.setdefault("seq", 0)
            d.setdefault("tasks", {})
            if isinstance(d["tasks"], dict):
                return d
    except (OSError, ValueError):
        pass
    return {"seq": 0, "tasks": {}}


def _save_tasks_locked(mutate):
    """flock-guarded read-modify-write so concurrent orchestrators can't lose each other's edits.
    `mutate(d)` edits the dict in place and returns the result handed back to the caller."""
    from . import config as _config

    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(TASKS_FILE + ".lock", "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        d = load_tasks()
        result = mutate(d)
        tmp = "%s.tmp.%d" % (TASKS_FILE, _config.os.getpid())
        with open(tmp, "w") as f:
            _config.json.dump(d, f, indent=2)
        _config.os.replace(tmp, TASKS_FILE)
        return result
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass


def task_add(title, project=None, status="todo", note=None):
    from . import config as _config

    title = (title or "").strip()
    if not title:
        return None

    def m(d):
        d["seq"] += 1
        tid = "t%d" % d["seq"]
        d["tasks"][tid] = {
            "id": tid,
            "title": title,
            "project": project,
            "status": status if status in TASK_STATUSES else "todo",
            "created": int(_config.time.time()),
            "updated": int(_config.time.time()),
            "notes": [note] if note else [],
            "runs": [],
        }
        return tid

    return _save_tasks_locked(m)


def task_update(tid, status=None, title=None, project=None, note=None, run=None):
    from . import config as _config

    def m(d):
        t = d["tasks"].get(tid)
        if not t:
            return None
        if status and status in TASK_STATUSES:
            t["status"] = status
        if title:
            t["title"] = title.strip()
        if project is not None:
            t["project"] = project or None
        if note:
            t.setdefault("notes", []).append(note)
        if run and run not in t.setdefault("runs", []):
            t["runs"].append(run)
        t["updated"] = int(_config.time.time())
        return t

    return _save_tasks_locked(m)


def task_remove(tid):
    return _save_tasks_locked(lambda d: d["tasks"].pop(tid, None))


def tasks_for_project(project=None, include_done=False):
    """Open (or all) tasks, newest first; filtered to a project when given."""
    out = []
    for t in load_tasks()["tasks"].values():
        if project is not None and (t.get("project") or None) != project:
            continue
        if not include_done and t.get("status") in TASK_DONE_STATUSES:
            continue
        out.append(t)
    out.sort(key=lambda t: t.get("updated", 0), reverse=True)
    return out


_TASK_STATUS_VERB = {
    "done": "done",
    "start": "doing",
    "doing": "doing",
    "block": "blocked",
    "blocked": "blocked",
    "drop": "dropped",
    "dropped": "dropped",
    "reopen": "todo",
    "todo": "todo",
    "merge": "merged",
    "merged": "merged",
}


def cmd_task(argv):
    """The orchestrator's durable backlog — ONE source of truth for what's pending vs done, so it
    never re-does resolved work or loops. Project-tagged (auto from cwd), shown on the dashboard.
       neomax task                       list THIS project's open tasks (todo/doing/blocked)
       neomax task list [--all] [--all-projects] [--project P] [--json]
       neomax task add "<title>" [--project P] [--note "..."] [--status S]
       neomax task done|start|block|drop|reopen <id>...   move task(s) to that status
       neomax task status <id> <status>  (todo|doing|blocked|done|merged|dropped)
       neomax task note <id> "..."       append a note
       neomax task link <id> <run-id>     associate a delegated run
       neomax task rm <id>...             delete"""
    from . import config as _config
    from . import models as _models
    from . import projects as _projects

    sub = argv[0] if argv else "list"
    rest = argv[1:]

    def opt(name, default=None):
        if name in rest:
            i = rest.index(name)
            return rest[i + 1] if i + 1 < len(rest) else default
        return default

    if sub == "add":
        (toks, skip) = ([], False)
        for i, a in enumerate(rest):
            if skip:
                skip = False
                continue
            if a in ("--project", "--note", "--status"):
                skip = True
                continue
            if a.startswith("--"):
                continue
            toks.append(a)
        title = " ".join(toks)
        proj = opt("--project")
        if proj is None:
            proj = _projects.project_of(_config.os.getcwd())
        tid = task_add(
            title, project=proj, status=opt("--status", "todo"), note=opt("--note")
        )
        if not tid:
            _models.err("neomax task add: need a title")
            _config.sys.exit(2)
        print("task %s added%s: %s" % (tid, " [%s]" % proj if proj else "", title))
        return
    if sub in _TASK_STATUS_VERB and rest:
        st = _TASK_STATUS_VERB[sub]
        for tid in [a for a in rest if not a.startswith("--")]:
            if task_update(tid, status=st):
                print("task %s → %s" % (tid, st))
            else:
                _models.err("neomax task: no task %s" % tid)
        return
    if sub == "status" and len(rest) >= 2:
        (tid, st) = (rest[0], rest[1])
        if st not in TASK_STATUSES:
            _models.err(
                "neomax task status: status must be one of %s"
                % ", ".join(TASK_STATUSES)
            )
            _config.sys.exit(2)
        if task_update(tid, status=st):
            print("task %s → %s" % (tid, st))
        else:
            _models.err("neomax task: no task %s" % tid)
        return
    if sub == "note" and len(rest) >= 2:
        tid = rest[0]
        note = " ".join(rest[1:])
        if task_update(tid, note=note):
            print("task %s: noted" % tid)
        else:
            _models.err("neomax task: no task %s" % tid)
        return
    if sub == "link" and len(rest) >= 2:
        if task_update(rest[0], run=rest[1]):
            print("task %s linked to run %s" % (rest[0], rest[1]))
        else:
            _models.err("neomax task: no task %s" % rest[0])
        return
    if sub == "rm" and rest:
        for tid in [a for a in rest if not a.startswith("--")]:
            print("task %s removed" % tid if task_remove(tid) else "no task %s" % tid)
        return
    want_json = "--json" in rest
    include_done = "--all" in rest
    all_projects = "--all-projects" in rest
    proj = opt("--project")
    if proj is None and (not all_projects):
        proj = _projects.project_of(_config.os.getcwd())
    rows = tasks_for_project(
        project=None if all_projects else proj, include_done=include_done
    )
    if want_json:
        print(_config.json.dumps(rows, indent=2))
        return
    if not rows:
        scope = (
            "any project"
            if all_projects
            else "project '%s'" % proj
            if proj
            else "this location"
        )
        print(
            'no %s tasks for %s — add one: neomax task add "<title>"'
            % ("" if include_done else "open", scope)
        )
        return
    hdr = (
        "TASKS"
        + (" (all)" if include_done else " — open")
        + (" · all projects" if all_projects else " · %s" % proj if proj else "")
    )
    print(hdr)
    by_status = {}
    for t in rows:
        by_status.setdefault(t.get("status", "todo"), []).append(t)
    order = ("doing", "blocked", "todo", "done", "merged", "dropped")
    for st in order:
        for t in by_status.get(st, []):
            pj = " [%s]" % t["project"] if all_projects and t.get("project") else ""
            runs = " ←%s" % ",".join(t.get("runs", [])[:2]) if t.get("runs") else ""
            print(
                "  %-3s %-8s %s%s%s"
                % (t["id"], st.upper(), t.get("title", ""), pj, runs)
            )
