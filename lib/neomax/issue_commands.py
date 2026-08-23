"""Issue and CI command handlers."""


def _issue_argparse(rest):
    (opts, positional, i) = ({}, [], 0)
    BOOL = {"--all", "--json", "--force", "--apply", "--force-new"}
    while i < len(rest):
        a = rest[i]
        if a.startswith("--"):
            if a in BOOL:
                opts[a] = True
                i += 1
            else:
                opts[a] = rest[i + 1] if i + 1 < len(rest) else ""
                i += 2
        else:
            positional.append(a)
            i += 1
    return (opts, positional)


def cmd_issue(argv):
    """Cross-repo GitHub-issue ledger — the /find-issues + /fix-issues spine.
    neomax issue open --title T [--body B] [--repos a,b | --all] [--severity S] [--project P]
    neomax issue list [--json] [--status open|claimed|fixing|pr|done] [--project P]
    neomax issue show KEY [--json]
    neomax issue next [--json] [--project P] [--all | --batch N]   (CLAIM open issue(s) — fix loop; --all/--batch take MANY at once)
    neomax issue claim KEY  |  issue release KEY
    neomax issue set KEY --status S
    neomax issue link KEY [--run RID] [--pr REPO=URL]
    neomax issue comment KEY "text"              (sync a note to every mirror)
    neomax issue close KEY [--comment "..."]
    neomax issue reconcile [--project P]"""
    from . import config as _config
    from . import cross_repo_issues as _cross_repo_issues
    from . import github_client as _github_client
    from . import issue_ci as _issue_ci
    from . import issue_store as _issue_store
    from . import models as _models
    from . import projects as _projects

    if not argv:
        _models.err(
            "usage: neomax issue <open|list|show|next|claim|release|set|link|comment|close|reconcile> ..."
        )
        _config.sys.exit(2)
    (sub, rest) = (argv[0], argv[1:])
    (opts, positional) = _issue_argparse(rest)
    key0 = positional[0] if positional else None
    if sub == "open":
        title = opts.get("--title") or key0
        if not title:
            _models.err("issue open: --title required")
            _config.sys.exit(2)
        project = opts.get("--project") or _projects.project_of(_config.os.getcwd())
        if not project:
            _models.err("issue open: not in a registered project — pass --project")
            _config.sys.exit(2)
        repos = (
            None
            if opts.get("--all")
            else [r.strip() for r in opts["--repos"].split(",") if r.strip()]
            if opts.get("--repos")
            else None
        )
        try:
            iss = _cross_repo_issues.open_cross_repo_issue(
                title,
                opts.get("--body", ""),
                project,
                repos,
                severity=opts.get("--severity"),
                fingerprint=opts.get("--fingerprint"),
                force_new=bool(opts.get("--force-new")),
            )
        except ValueError as e:
            _models.err("issue open: %s" % e)
            _config.sys.exit(2)
        if iss.get("_new") is False:
            _models.err(
                "NEOMAX ISSUE deduped — already tracked as %s (use --force-new to override)"
                % iss["key"]
            )
            print(iss["key"])
            return
        mirrors = sum((1 for e in iss["repos"].values() if e.get("url")))
        _models.err(
            "NEOMAX ISSUE key=%s project=%s repos=%d gh-mirrors=%d"
            % (iss["key"], project, len(iss["repos"]), mirrors)
        )
        print(iss["key"])
        return
    if sub == "list":
        items = _issue_store.list_issues(
            project=opts.get("--project") or _projects.project_of(_config.os.getcwd()),
            status=opts.get("--status"),
        )
        if opts.get("--json"):
            print(_config.json.dumps(items, indent=2))
            return
        if not items:
            print("(no issues)")
            return
        for i in items:
            mr = (
                ",".join(
                    (
                        "%s#%s" % (n, e.get("number"))
                        for (n, e) in (i.get("repos") or {}).items()
                        if e.get("number")
                    )
                )
                or "local"
            )
            held = " *claimed*" if _issue_store._claim_active(i) else ""
            print(
                "%-26s [%-7s] %s  (%s)%s"
                % (i["key"], i["status"], (i["title"] or "")[:58], mr, held)
            )
        return
    if sub == "show":
        iss = _issue_store.load_issue(key0) if key0 else None
        if not iss:
            _models.err("issue show: unknown key %r" % key0)
            _config.sys.exit(1)
        if opts.get("--json"):
            print(_config.json.dumps(iss, indent=2))
            return
        print("%s  [%s]  %s" % (iss["key"], iss["status"], iss["title"]))
        print("project: %s   severity: %s" % (iss.get("project"), iss.get("severity")))
        for n, e in (iss.get("repos") or {}).items():
            print("  repo %-22s %s" % (n, e.get("url") or "(local, no PR mirror)"))
        if iss.get("runs"):
            print("  runs: %s" % ", ".join(iss["runs"]))
        if iss.get("prs"):
            print(
                "  PRs:  %s"
                % ", ".join(("%s=%s" % (k, v) for (k, v) in iss["prs"].items()))
            )
        return
    if sub == "next":
        batched = bool(opts.get("--all") or opts.get("--batch"))
        if opts.get("--all"):
            limit = 10**9
        elif opts.get("--batch"):
            try:
                limit = max(1, int(opts["--batch"]))
            except (TypeError, ValueError):
                _models.err("issue next: --batch needs an integer")
                _config.sys.exit(2)
        else:
            limit = 1
        proj = opts.get("--project") or _projects.project_of(_config.os.getcwd())
        cands = [
            i
            for i in _issue_store.list_issues(project=proj)
            if i.get("status") == "open"
            or (
                i.get("status") in ("claimed", "fixing")
                and (not _issue_store._claim_active(i))
            )
        ]
        claimed = []
        for cand in cands:
            if len(claimed) >= limit:
                break
            got = _issue_store.claim_issue(cand["key"], take=True)
            if got:
                claimed.append(got)
        if opts.get("--json"):
            payload = [
                {
                    "key": g["key"],
                    "title": g["title"],
                    "project": g["project"],
                    "repos": g["repos"],
                    "brief": _issue_ci._issue_brief(g),
                }
                for g in claimed
            ]
            print(
                _config.json.dumps(
                    payload if batched else payload[0] if payload else None, indent=2
                )
            )
        elif not claimed:
            print("(no open unclaimed issues)")
        else:
            for g in claimed:
                print(g["key"])
            _models.err(
                "claimed %d issue(s):\n%s"
                % (
                    len(claimed),
                    "\n\n".join((_issue_ci._issue_brief(g) for g in claimed)),
                )
            )
        return
    if sub in ("claim", "release"):
        if not key0:
            _models.err("issue %s: KEY required" % sub)
            _config.sys.exit(2)
        iss = _issue_store.claim_issue(key0, take=sub == "claim")
        if iss is None:
            _models.err(
                "issue %s: %s is held by another live session, or unknown" % (sub, key0)
            )
            _config.sys.exit(1)
        print("%sed %s" % (sub, key0))
        return
    if sub == "set":
        iss = _issue_store.load_issue(key0) if key0 else None
        if not iss:
            _models.err("issue set: unknown key %r" % key0)
            _config.sys.exit(1)
        st = opts.get("--status")
        if st not in _github_client.ISSUE_STATUSES:
            _models.err(
                "issue set: --status must be one of %s"
                % "|".join(_github_client.ISSUE_STATUSES)
            )
            _config.sys.exit(2)
        iss["status"] = st
        _issue_store.issue_event(iss, "status", to=st)
        _issue_store.save_issue(iss)
        print("%s -> %s" % (key0, st))
        return
    if sub == "link":
        iss = _issue_store.load_issue(key0) if key0 else None
        if not iss:
            _models.err("issue link: unknown key %r" % key0)
            _config.sys.exit(1)
        if opts.get("--run"):
            if opts["--run"] not in iss.setdefault("runs", []):
                iss["runs"].append(opts["--run"])
            if iss["status"] in ("open", "claimed"):
                iss["status"] = "fixing"
            _issue_store.issue_event(iss, "linked-run", run=opts["--run"])
        if opts.get("--pr") and "=" in opts["--pr"]:
            (rname, url) = opts["--pr"].split("=", 1)
            iss.setdefault("prs", {})[rname] = url
            iss["status"] = "pr"
            _issue_store.issue_event(iss, "linked-pr", repo=rname, url=url)
        _issue_store.save_issue(iss)
        print("linked %s" % key0)
        return
    if sub == "comment":
        iss = _issue_store.load_issue(key0) if key0 else None
        if not iss:
            _models.err("issue comment: unknown key %r" % key0)
            _config.sys.exit(1)
        text = positional[1] if len(positional) > 1 else opts.get("--body")
        if not text:
            _models.err("issue comment: text required")
            _config.sys.exit(2)
        n = _cross_repo_issues.issue_comment_all(iss, text)
        _issue_store.save_issue(iss)
        print("commented on %d mirror(s)" % n)
        return
    if sub == "close":
        iss = _issue_store.load_issue(key0) if key0 else None
        if not iss:
            _models.err("issue close: unknown key %r" % key0)
            _config.sys.exit(1)
        _cross_repo_issues.close_cross_repo_issue(iss, comment=opts.get("--comment"))
        print("closed %s" % key0)
        return
    if sub == "reconcile":
        n = _cross_repo_issues.reconcile_issues(
            project=opts.get("--project") or _projects.project_of(_config.os.getcwd())
        )
        print("issue reconcile: %d marked done (all mirrors closed on GitHub)" % n)
        return
    _models.err("issue: unknown subcommand %r" % sub)
    _config.sys.exit(2)


def cmd_ci_sync(argv):
    """Install the standard neomax-ci GitHub Actions workflow into every repo of a project, so the
    cross-repo fix pipeline has uniform CI. `neomax ci-sync [--project P] [--apply] [--force]`
    — dry run without --apply."""
    from . import config as _config
    from . import issue_ci as _issue_ci
    from . import models as _models
    from . import projects as _projects

    (opts, _) = _issue_argparse(argv)
    project = opts.get("--project") or _projects.project_of(_config.os.getcwd())
    if not project:
        _models.err("ci-sync: not in a registered project — pass --project")
        _config.sys.exit(2)
    rep = _issue_ci.ci_sync(
        project, apply=bool(opts.get("--apply")), force=bool(opts.get("--force"))
    )
    if not rep:
        _models.err("ci-sync: project %s has no repos on disk" % project)
        _config.sys.exit(1)
    for name, action in rep:
        print("  %-24s %s" % (name, action))
    if not opts.get("--apply"):
        _models.err(
            "ci-sync: DRY RUN — re-run with --apply to write the files, then PR them per repo."
        )
    return
