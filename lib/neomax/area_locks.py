"""Scheduler area locks and conflict boundaries."""

from . import config as _config

PLANS_DIR = _config.os.path.join(_config.STATE_DIR, "plans")
LOCKS_DIR = _config.os.path.join(_config.STATE_DIR, "locks")


def affected_area(path):
    """Map a file path → a coarse 'area' key (ports ClawSweeper affectedAreaForFile),
    so two parts touching the same area serialize while disjoint areas parallelize."""
    p = [x for x in path.split("/") if x]
    if not p:
        return "*"
    if p[0] in ("apps", "packages") and len(p) >= 2:
        return p[0] + "/" + p[1]
    if p[0] == "src" and len(p) >= 2:
        return "src/" + p[1]
    if p[0] in ("test", "tests", "docs", ".github", "scripts"):
        return p[0]
    return p[0]


def _lock_path(repo, area):
    from . import config as _config

    h = _config.hashlib.sha256(_config.os.path.abspath(repo).encode()).hexdigest()[:12]
    d = _config.os.path.join(LOCKS_DIR, h)
    _config.os.makedirs(d, exist_ok=True)
    return _config.os.path.join(d, area.replace("/", "__") + ".lock")


def _lock_stale(holder):
    from . import config as _config
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    if not holder:
        return True
    rid = holder.get("runid")
    if rid:
        rec = _runs.load_run(rid)
        if rec is not None:
            return _orchestrators.effective_status(
                rec
            ) in _orchestrators.TERMINAL_STATUSES or (
                not _orchestrators.worker_alive(rec)
                and (not _runs.pid_alive(rec.get("pid")))
            )
    pid = holder.get("pid")
    if pid and (not _runs.pid_alive(pid)):
        return True
    return bool(holder.get("ts") and _config.time.time() - holder["ts"] > 6 * 3600)


def _glob_locks(repo):
    from . import config as _config

    h = _config.hashlib.sha256(_config.os.path.abspath(repo).encode()).hexdigest()[:12]
    return _config.globmod.glob(_config.os.path.join(LOCKS_DIR, h, "*.lock"))


def acquire_area_lock(repo, area, runid):
    """Atomic per-area lock (O_CREAT|O_EXCL). Reclaims a stale lock (dead/terminal/
    torn owner). '*' is GLOBAL-EXCLUSIVE (blocks, and is blocked by, any other area).
    Returns True on acquire, False if genuinely held. Never raises (disk-full → False)."""
    from . import config as _config

    starlock = _lock_path(repo, "*")

    def _conflict_present():
        if area == "*":
            for other in _glob_locks(repo):
                if other == starlock:
                    continue
                try:
                    h = _config.json.load(open(other))
                except (OSError, ValueError):
                    h = {}
                if h.get("runid") != runid and (not _lock_stale(h)):
                    return True
            return False
        if _config.os.path.exists(starlock):
            try:
                h = _config.json.load(open(starlock))
            except (OSError, ValueError):
                h = {}
            if h.get("runid") != runid and (not _lock_stale(h)):
                return True
        return False

    p = _lock_path(repo, area)
    for _ in range(2):
        try:
            fd = _config.os.open(
                p, _config.os.O_CREAT | _config.os.O_EXCL | _config.os.O_WRONLY, 420
            )
            _config.os.write(
                fd,
                _config.json.dumps(
                    {
                        "runid": runid,
                        "pid": _config.os.getpid(),
                        "ts": int(_config.time.time()),
                    }
                ).encode(),
            )
            _config.os.close(fd)
            if _conflict_present():
                try:
                    _config.os.remove(p)
                except OSError:
                    pass
                return False
            return True
        except FileExistsError:
            try:
                holder = _config.json.load(open(p))
            except (OSError, ValueError):
                holder = {}
            if holder.get("runid") == runid:
                return True
            if _lock_stale(holder):
                try:
                    _config.os.remove(p)
                except OSError:
                    pass
                continue
            return False
        except OSError:
            return False
    return False


def release_area_locks(repo, areas, runid):
    from . import config as _config

    for area in areas:
        p = _lock_path(repo, area)
        try:
            if _config.json.load(open(p)).get("runid") == runid:
                _config.os.remove(p)
        except (OSError, ValueError):
            pass


UNION_SAFE_RE = _config.re.compile(
    "(^|/)CHANGELOG(\\.[a-z]+)?$|(^|/)\\.gitignore$|(^|/)\\.neomax/|\\.(log|ndjson)$",
    _config.re.I,
)
