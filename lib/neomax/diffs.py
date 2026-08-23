"""Run-level exact diff reporting."""


def cmd_diff(argv):
    """The REAL git diff of an orchestrator run's branch vs its base — numstat summary
    + optional patch. Works for live runs AND archived history (record kept in
    history.db). `neomax diff <runid> [--json] [--patch]`."""
    from . import config as _config
    from . import gitops as _gitops
    from . import models as _models
    from . import runs as _runs

    if not argv:
        _models.err("neomax: diff <runid> [--json] [--patch]")
        _config.sys.exit(2)
    rid = argv[0]
    rec = _runs.load_run(rid)
    if not rec:
        c = _runs.history_conn()
        if c is not None:
            try:
                row = c.execute("SELECT record FROM runs WHERE id=?", (rid,)).fetchone()
                if row and row[0]:
                    rec = _config.json.loads(row[0])
            except Exception:
                pass
    if not rec:
        _models.err("neomax: unknown run %s" % rid)
        _config.sys.exit(1)
    (repo, branch, base) = (
        rec.get("repo"),
        rec.get("branch"),
        rec.get("base") or "HEAD",
    )
    if not repo or not branch or (not _config.os.path.isdir(repo)):
        _models.err(
            "neomax: run %s has no diffable repo/branch (repo=%s branch=%s)"
            % (rid, repo, branch)
        )
        _config.sys.exit(1)
    if _gitops.git(["rev-parse", "--verify", branch], repo).returncode != 0:
        _models.err(
            "neomax: branch %s no longer exists in %s (merged+cleaned?)"
            % (branch, repo)
        )
        _config.sys.exit(1)
    merge_base = _gitops.git(["merge-base", base, branch], repo).stdout.strip() or base
    ns = _gitops.git(["diff", "--numstat", merge_base, branch], repo).stdout
    files = []
    for ln in ns.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3:
            (a, d, path) = parts
            files.append(
                {
                    "path": path,
                    "adds": int(a) if a.isdigit() else 0,
                    "dels": int(d) if d.isdigit() else 0,
                }
            )
    patch = None
    if "--patch" in argv or "--json" in argv:
        patch = _gitops.git(["diff", merge_base, branch], repo).stdout
        if patch and len(patch) > 400000:
            patch = patch[:400000] + "\n... (truncated at 400KB)"
    if "--json" in argv:
        print(
            _config.json.dumps(
                {
                    "id": rid,
                    "repo": _config.os.path.basename(repo),
                    "branch": branch,
                    "base": merge_base[:12],
                    "files": files,
                    "adds": sum((x["adds"] for x in files)),
                    "dels": sum((x["dels"] for x in files)),
                    "patch": patch,
                }
            )
        )
        return
    print("diff %s (%s vs %s) — %d files" % (branch, rid, merge_base[:12], len(files)))
    for x in files:
        print("  +%-5d -%-5d %s" % (x["adds"], x["dels"], x["path"]))
    if "--patch" in argv and patch:
        print(patch)
