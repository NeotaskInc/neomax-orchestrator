"""Cross-repository issue lifecycle."""


def open_cross_repo_issue(
    title,
    body,
    project,
    repo_names,
    severity=None,
    labels=None,
    key=None,
    fingerprint=None,
    force_new=False,
):
    """Create a CANONICAL issue mirrored as a GitHub issue in each affected repo (cross-linked by a
    shared key + the neomax-issue label), recorded in the durable ledger. gh-optional: a repo with no
    gh/remote gets a LOCAL-ONLY mirror (number=None) so the issue is still tracked + fixable.
    DEDUPED by fingerprint: if a non-terminal issue with the same fingerprint exists, that one is
    returned (no duplicate, no new GitHub issues) with `_new=False`. Returns the issue dict (carrying
    a transient, unsaved `_new` flag)."""
    from . import config as _config
    from . import github_client as _github_client
    from . import issue_store as _issue_store

    key = (
        key
        or "iss-"
        + _config.time.strftime("%Y%m%d-%H%M%S")
        + "-"
        + _config.os.urandom(3).hex()
    )
    labels = sorted(set([_github_client.ISSUE_LABEL] + list(labels or [])))
    paths = {n: p for (n, p) in _github_client.project_repo_paths(project)}
    if repo_names:
        unknown = [n for n in repo_names if n not in paths]
        if unknown:
            raise ValueError(
                "unknown repo(s) %s for project %r — valid repos: %s"
                % (unknown, project, ", ".join(sorted(paths)) or "(none)")
            )
        targets = [n for n in repo_names if n in paths]
    else:
        targets = list(paths)
    fp = fingerprint or _issue_store.issue_fingerprint(title, project, targets)
    _config.os.makedirs(_github_client.ISSUES_DIR, exist_ok=True)
    lf = None
    try:
        lf = open(_config.os.path.join(_github_client.ISSUES_DIR, ".open.lock"), "w")
        _config.fcntl.flock(lf, _config.fcntl.LOCK_EX)
    except OSError:
        lf = None
    try:
        if not force_new:
            dup = _issue_store.find_open_duplicate(fp, project)
            if dup:
                dup["_new"] = False
                return dup
        return _create_cross_repo_issue(
            key, title, body, project, targets, paths, severity, labels, fp
        )
    finally:
        if lf is not None:
            try:
                _config.fcntl.flock(lf, _config.fcntl.LOCK_UN)
                lf.close()
            except OSError:
                pass


def _create_cross_repo_issue(
    key, title, body, project, targets, paths, severity, labels, fp
):
    """Mint + persist a fresh cross-repo issue (GitHub mirrors + ledger record). Called under the
    open lock by open_cross_repo_issue once dedup has confirmed there's no existing match."""
    from . import config as _config
    from . import github_client as _github_client
    from . import gitops as _gitops
    from . import issue_store as _issue_store

    iss = {
        "key": key,
        "title": title,
        "body": body or "",
        "project": project,
        "severity": severity,
        "labels": labels,
        "status": "open",
        "claim": None,
        "fingerprint": fp,
        "created": int(_config.time.time()),
        "updated": int(_config.time.time()),
        "repos": {},
        "runs": [],
        "prs": {},
        "history": [],
    }
    canon = (
        "\n\n<!-- neomax-issue:%s -->\n_Cross-repo issue `%s` (project `%s`) — affects: %s._"
        % (key, key, project, ", ".join(targets))
    )
    for name in targets:
        repo = paths[name]
        entry = {"number": None, "url": None, "state": "local"}
        if _github_client.gh_available() and _gitops.has_remote(repo):
            for lb in labels:
                _github_client.gh_run(["label", "create", lb, "--force"], repo)
            mk = _github_client.gh_run(
                [
                    "issue",
                    "create",
                    "--title",
                    title,
                    "--body",
                    (body or "") + canon,
                    "--label",
                    ",".join(labels),
                ],
                repo,
            )
            lines = (mk.stdout or "").strip().splitlines()
            url = lines[-1] if mk.returncode == 0 and lines else ""
            if url.startswith("http"):
                entry = {
                    "number": url.rstrip("/").split("/")[-1],
                    "url": url,
                    "state": "open",
                }
        iss["repos"][name] = entry
    sib = {n: e["url"] for (n, e) in iss["repos"].items() if e.get("url")}
    if len(sib) > 1:
        for name, e in iss["repos"].items():
            if not e.get("number"):
                continue
            others = "\n".join(
                ("- %s: %s" % (n, u) for (n, u) in sib.items() if n != name)
            )
            _github_client.gh_run(
                [
                    "issue",
                    "comment",
                    str(e["number"]),
                    "--body",
                    "🔗 Synced cross-repo issue `%s`. Sibling issues:\n%s"
                    % (key, others),
                ],
                paths[name],
            )
    _issue_store.issue_event(iss, "opened", repos=list(targets))
    _issue_store.save_issue(iss)
    iss["_new"] = True
    return iss


def issue_comment_all(iss, text):
    """Post a comment on every GitHub mirror of an issue (progress/report sync). Best-effort."""
    from . import github_client as _github_client

    paths = {n: p for (n, p) in _github_client.project_repo_paths(iss.get("project"))}
    n_posted = 0
    for name, e in (iss.get("repos") or {}).items():
        if e.get("number") and name in paths:
            r = _github_client.gh_run(
                ["issue", "comment", str(e["number"]), "--body", text], paths[name]
            )
            if r.returncode == 0:
                n_posted += 1
    return n_posted


def close_cross_repo_issue(iss, comment=None):
    """Close every GitHub mirror + mark the ledger record done. Best-effort per repo."""
    from . import github_client as _github_client
    from . import issue_store as _issue_store

    paths = {n: p for (n, p) in _github_client.project_repo_paths(iss.get("project"))}
    for name, e in (iss.get("repos") or {}).items():
        if e.get("number") and name in paths:
            args = ["issue", "close", str(e["number"])]
            if comment:
                args += ["--comment", comment]
            if _github_client.gh_run(args, paths[name]).returncode == 0:
                e["state"] = "closed"
    iss["status"] = "done"
    _issue_store.issue_event(iss, "closed")
    _issue_store.save_issue(iss)
    return iss


def reconcile_issues(project=None):
    """Sync the ledger with GitHub: if every mirror of an open issue is closed on GitHub, mark the
    ledger record done (so the fix loop + dashboard reflect reality). Graceful without gh."""
    from . import github_client as _github_client
    from . import issue_store as _issue_store

    changed = 0
    for iss in _issue_store.list_issues(project=project):
        if iss.get("status") in ("done", "closed"):
            continue
        paths = {
            n: p for (n, p) in _github_client.project_repo_paths(iss.get("project"))
        }
        mirrors = [
            (n, e) for (n, e) in (iss.get("repos") or {}).items() if e.get("number")
        ]
        if not mirrors or not _github_client.gh_available():
            continue
        states = []
        dirty = False
        for name, e in mirrors:
            if name not in paths:
                continue
            info = _github_client.gh_json(
                ["issue", "view", str(e["number"]), "--json", "state"], paths[name]
            )
            st = (info or {}).get("state", "").lower()
            if st:
                if e.get("state") != st:
                    dirty = True
                e["state"] = st
                states.append(st)
        if states and all((s == "closed" for s in states)):
            iss["status"] = "done"
            _issue_store.issue_event(iss, "auto-closed (all mirrors closed on GitHub)")
            changed += 1
            dirty = True
        if dirty:
            _issue_store.save_issue(iss)
    return changed
