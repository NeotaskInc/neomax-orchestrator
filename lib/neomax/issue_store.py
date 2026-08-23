"""Durable issue storage, claiming, and deduplication."""


def _issue_path(key):
    from . import config as _config
    from . import github_client as _github_client

    return _config.os.path.join(_github_client.ISSUES_DIR, "%s.json" % key)


def load_issue(key):
    from . import config as _config

    try:
        with open(_issue_path(key)) as f:
            return _config.json.load(f)
    except (OSError, ValueError):
        return None


def save_issue(iss):
    from . import config as _config
    from . import github_client as _github_client

    _config.os.makedirs(_github_client.ISSUES_DIR, exist_ok=True)
    iss["updated"] = int(_config.time.time())
    tmp = "%s.tmp.%d" % (_issue_path(iss["key"]), _config.os.getpid())
    with open(tmp, "w") as f:
        _config.json.dump(iss, f, indent=2)
    _config.os.replace(tmp, _issue_path(iss["key"]))


def list_issues(project=None, status=None):
    from . import config as _config
    from . import github_client as _github_client

    out = []
    try:
        names = sorted(_config.os.listdir(_github_client.ISSUES_DIR))
    except OSError:
        names = []
    for n in names:
        if not n.endswith(".json"):
            continue
        iss = load_issue(n[:-5])
        if (
            not iss
            or (project and iss.get("project") != project)
            or (status and iss.get("status") != status)
        ):
            continue
        out.append(iss)
    out.sort(key=lambda i: i.get("created", 0))
    return out


def issue_event(iss, event, **extra):
    """Append to the issue's own history AND the durable append-only audit trail. Best-effort."""
    from . import config as _config

    try:
        iss.setdefault("history", []).append(
            dict(ts=int(_config.time.time()), event=event, **extra)
        )
    except Exception:
        pass
    try:
        _config.os.makedirs(_config.EVENTS_DIR, exist_ok=True)
        entry = {
            "ts": int(_config.time.time()),
            "issue": iss.get("key"),
            "event": event,
            "project": iss.get("project"),
            "status": iss.get("status"),
        }
        entry.update(extra)
        day = _config.time.strftime("%Y-%m-%d", _config.time.localtime())
        with open(_config.os.path.join(_config.EVENTS_DIR, day + ".jsonl"), "a") as f:
            f.write(_config.json.dumps(entry) + "\n")
    except (OSError, ValueError):
        pass


def _session_live(session):
    from . import orchestrators as _orchestrators

    if not session:
        return False
    return any(
        (
            o.get("session") == session and o.get("live")
            for o in _orchestrators.all_orchestrators()
        )
    )


def _claim_active(iss):
    """True if the issue is currently claimed by a still-live session within the claim TTL."""
    from . import config as _config
    from . import github_client as _github_client
    from . import runs as _runs

    claim = iss.get("claim")
    if not claim:
        return False
    if _config.time.time() - claim.get("ts", 0) > _github_client.ISSUE_CLAIM_TTL_S:
        return False
    sess = claim.get("session")
    if sess and (not sess.startswith("pid-")):
        return _session_live(sess)
    return _runs.pid_alive(claim.get("pid"))


def claim_issue(key, take=True):
    """flock-guarded claim/release so two concurrent /fix-issues loops never grab the same issue.
    take=True → claim for THIS session (returns the issue, or None if already held by another LIVE
    session); take=False → release. A stale claim (dead session / past TTL) is reclaimable."""
    from . import config as _config
    from . import github_client as _github_client
    from . import orchestrators as _orchestrators

    _config.os.makedirs(_github_client.ISSUES_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(_config.os.path.join(_github_client.ISSUES_DIR, ".claim.lock"), "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        iss = load_issue(key)
        if not iss:
            return None
        sess = _orchestrators.current_orch_session() or "pid-%d" % _config.os.getpid()
        if take:
            if _claim_active(iss) and (iss.get("claim") or {}).get("session") != sess:
                return None
            iss["claim"] = {
                "session": sess,
                "pid": _config.os.getpid(),
                "ts": int(_config.time.time()),
            }
            if iss.get("status") == "open":
                iss["status"] = "claimed"
            issue_event(iss, "claimed", session=sess)
        else:
            iss["claim"] = None
            if iss.get("status") in ("claimed", "fixing"):
                iss["status"] = "open"
            issue_event(iss, "released")
        save_issue(iss)
        return iss
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass


def issue_fingerprint(title, project, repos):
    """A stable dedupe key for an issue: project + affected repo set + normalized title. Lets
    /find-issues be idempotent — re-detecting an UNFIXED issue never creates a duplicate."""
    from . import config as _config

    norm = _config.re.sub("[^a-z0-9]+", " ", (title or "").lower()).strip()
    return "%s::%s::%s" % (project or "", ",".join(sorted(repos or [])), norm)


def find_open_duplicate(fingerprint, project):
    """An existing NON-TERMINAL (not done/closed) issue with this fingerprint, or None. A done issue
    never blocks re-opening — if the same defect recurs after a fix, it can be filed again."""
    for iss in list_issues(project=project):
        if iss.get("status") in ("done", "closed"):
            continue
        if iss.get("fingerprint") and iss.get("fingerprint") == fingerprint:
            return iss
    return None
