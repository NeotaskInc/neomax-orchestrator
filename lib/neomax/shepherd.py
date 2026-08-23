"""Continuous-integration classification and merge shepherd."""

CI_NONRUN_CONCLUSIONS = {"STARTUP_FAILURE", "ACTION_REQUIRED", "STALE", "CANCELLED"}
CI_REAL_FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT"}


def classify_ci_checks(rollup):
    """Split a PR's statusCheckRollup into (real_failures, nonrun, pending) by check NAME.
    A CI run that could not execute because of GitHub Actions billing / spending-limit /
    startup failure — is NOT a real failure and must NEVER block a PR/merge; only a check that
    actually RAN and FAILED (a genuine test failure) blocks. The exception keys SOLELY off the
    check CONCLUSION (a billing/over-budget stop never starts the job → STARTUP_FAILURE /
    ACTION_REQUIRED), NOT the check name/title text — `gh pr view --json statusCheckRollup` does
    not even expose title/summary, and a money-worded NAME on a check that genuinely ran+failed
    must still block. Tolerates both CheckRun (conclusion/status) and legacy StatusContext (state)
    shapes the rollup returns, and any malformed entry (degrade, never crash)."""
    (real, nonrun, pending) = ([], [], [])
    for c in rollup or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("context") or "check"
        concl = str(c.get("conclusion") or "").upper()
        state = str(c.get("state") or "").upper()
        if concl in ("SUCCESS", "NEUTRAL", "SKIPPED") or state == "SUCCESS":
            continue
        if concl in CI_NONRUN_CONCLUSIONS:
            nonrun.append(name)
        elif concl in CI_REAL_FAIL_CONCLUSIONS or state in ("FAILURE", "ERROR"):
            real.append(name)
        else:
            pending.append(name)
    return (real, nonrun, pending)


def cmd_shepherd(argv):
    """Merge-shepherd: report (or perform) readiness of an integration branch toward
    its base — a SHA-pinned, idempotent gate. Without --merge it only REPORTS
    (merged|stopped|blocked|waiting|ready); with --merge it merges a green PR (gh).
       neomax shepherd --branch BR [--base BASE] [--expect SHA] [--merge]"""
    from . import config as _config
    from . import gitops as _gitops
    from . import models as _models
    from . import premerge as _premerge

    branch = base = expect = None
    do_merge = False
    a = list(argv)
    while a:
        x = a.pop(0)
        if x == "--branch":
            branch = a.pop(0)
        elif x == "--base":
            base = a.pop(0)
        elif x == "--expect":
            expect = a.pop(0)
        elif x == "--merge":
            do_merge = True
        else:
            _premerge.usage()
    if not branch:
        _premerge.usage()
    r = _gitops.git(["rev-parse", "--show-toplevel"], _config.os.getcwd())
    if r.returncode != 0:
        _models.err("neomax: not in a git repo")
        _config.sys.exit(2)
    repo = r.stdout.strip()
    base = base or _gitops.default_branch(repo)
    head = _gitops.git(["rev-parse", branch], repo).stdout.strip()
    if not head:
        _models.err("neomax: branch %s not found" % branch)
        _config.sys.exit(1)
    if _gitops.git(
        ["merge-base", "--is-ancestor", branch, base], repo
    ).returncode == 0 and _gitops.git(
        ["rev-list", "--count", "%s..%s" % (base, branch)], repo
    ).stdout.strip() in ("", "0"):
        print("merged: %s is already in %s" % (branch, base))
        return
    if expect and head != expect:
        print(
            "stopped: HEAD of %s moved (%s != expected %s)"
            % (branch, head[:8], expect[:8])
        )
        return
    ahead = int(
        _gitops.git(
            ["rev-list", "--count", "%s..%s" % (base, branch)], repo
        ).stdout.strip()
        or "0"
    )
    if ahead == 0:
        print("nothing to merge: %s has no commits ahead of %s" % (branch, base))
        return
    if _config.shutil.which("gh") and _gitops.has_remote(repo):
        view = _config.subprocess.run(
            [
                "gh",
                "pr",
                "view",
                branch,
                "--json",
                "mergeStateStatus,state,url,statusCheckRollup",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        info = {}
        if view.returncode == 0 and view.stdout.strip():
            try:
                info = _config.json.loads(view.stdout)
            except ValueError:
                info = {}
        if info:
            url = info.get("url")
            if info.get("state") == "MERGED":
                print("merged: PR %s" % url)
                return
            ms = info.get("mergeStateStatus")
            if ms in ("DIRTY", "BEHIND"):
                print(
                    "blocked: PR needs rebase onto %s (mergeState=%s, %s)"
                    % (base, ms, url)
                )
                return
            rollup = info.get("statusCheckRollup") or []
            (real, nonrun, pending) = classify_ci_checks(rollup)
            if _config.os.environ.get("NEOMAX_CI_IGNORE_BILLING", "1") == "0":
                (real, nonrun) = (real + nonrun, [])
            if real:
                print(
                    "blocked: failing CI check(s): %s (%s)"
                    % (", ".join(sorted(set(real))), url)
                )
                return
            if pending:
                print(
                    "waiting: CI check(s) still running: %s (%s)"
                    % (", ".join(sorted(set(pending))), url)
                )
                return
            if not rollup and ms in ("UNKNOWN", "PENDING", "HAS_HOOKS", "UNSTABLE"):
                print("waiting: CI not reported yet (mergeState=%s, %s)" % (ms, url))
                return
            if ms == "BLOCKED":
                print(
                    "blocked: PR mergeState=BLOCKED (branch protection / required review / unresolved threads) (%s)"
                    % url
                )
                return
            note = (
                " [ignored non-running/billing CI: %s]" % ", ".join(sorted(set(nonrun)))
                if nonrun
                else ""
            )
            print("ready: PR %s is mergeable%s" % (url, note))
            if do_merge:
                mg = _config.subprocess.run(
                    ["gh", "pr", "merge", branch, "--merge"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                )
                print(
                    "merged"
                    if mg.returncode == 0
                    else "merge failed: " + mg.stderr.strip()[:200]
                )
            return
    print(
        "ready (local): %s is %d commit(s) ahead of %s. Open a PR: neomax pr --branch %s --base %s"
        % (branch, ahead, base, branch, base)
    )
