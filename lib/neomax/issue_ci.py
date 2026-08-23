"""Continuous-integration issue synchronization."""

NEOMAX_CI_WORKFLOW = "# Managed by Neomax `neomax ci-sync` — standard per-repo CI for the cross-repo fix pipeline.\n# Edit the test command for this repo if needed; ci-sync will not clobber a hand-edited file\n# unless you pass --force.\nname: neomax-ci\non:\n  push:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-node@v4\n        with:\n          node-version: '20'\n      - name: Install\n        run: |\n          if [ -f package-lock.json ]; then npm ci; \\\n          elif [ -f package.json ]; then npm install; \\\n          else echo \"no package.json — skipping install\"; fi\n      - name: Test\n        run: |\n          if [ -f package.json ] && npm run | grep -qE '^  test'; then npm test --if-present; \\\n          else echo \"no test script — skipping\"; fi\n"
NEOMAX_CI_SENTINEL = "Managed by Neomax `neomax ci-sync`"


def ci_sync(project=None, apply=False, force=False):
    """Install the standard neomax-ci workflow into every repo of a project (so the cross-repo fix
    pipeline has uniform CI). Idempotent: skips a repo whose workflow already matches; without
    --force it won't overwrite a file a human edited (one missing our sentinel). Returns a report
    list of (repo, action). Without --apply it's a dry run (action prefixed 'would-')."""
    from . import config as _config
    from . import github_client as _github_client

    report = []
    for name, repo in _github_client.project_repo_paths(project):
        wf_dir = _config.os.path.join(repo, ".github", "workflows")
        wf = _config.os.path.join(wf_dir, "neomax-ci.yml")
        existing = None
        try:
            with open(wf) as f:
                existing = f.read()
        except OSError:
            existing = None
        if existing == NEOMAX_CI_WORKFLOW:
            report.append((name, "unchanged"))
            continue
        if existing is not None and NEOMAX_CI_SENTINEL not in existing and (not force):
            report.append((name, "skip (hand-edited; --force to overwrite)"))
            continue
        action = "update" if existing is not None else "create"
        if not apply:
            report.append((name, "would-" + action))
            continue
        try:
            _config.os.makedirs(wf_dir, exist_ok=True)
            tmp = wf + ".tmp.%d" % _config.os.getpid()
            with open(tmp, "w") as f:
                f.write(NEOMAX_CI_WORKFLOW)
            _config.os.replace(tmp, wf)
            report.append((name, action + "d"))
        except OSError as e:
            report.append((name, "error: %s" % e))
    return report


def _issue_brief(iss):
    """A self-contained brief for a fix worker: what to fix, where (every affected repo), and the
    cross-repo + delivery rules. This is what /fix-issues hands its agent team."""
    repos = (
        ", ".join(
            (
                "%s%s" % (n, "" if e.get("url") else " (local)")
                for (n, e) in (iss.get("repos") or {}).items()
            )
        )
        or "(all project repos)"
    )
    urls = "\n".join(
        (
            "  - %s: %s" % (n, e.get("url") or "(local)")
            for (n, e) in (iss.get("repos") or {}).items()
        )
    )
    return (
        "OBJECTIVE — FIX cross-repo issue `%s` (project `%s`): %s\n\nCONTEXT/WHY:\n%s\n\nAFFECTED REPOS (the fix may span ANY/ALL of these — they work together; verify the fix holds ACROSS repos, not just one):\n%s\n\nMIRROR ISSUES:\n%s\n\nDELIVERY: land the change on a feature branch in EACH repo you touch, then drive each to a GREEN PR via /no-mistakes (push/PR freely; do NOT merge to main). Report the PR set. A CI run that can't execute for GitHub Actions billing reasons is NOT a failure."
        % (
            iss.get("key"),
            iss.get("project"),
            iss.get("title"),
            iss.get("body") or "(none)",
            repos,
            urls,
        )
    )
