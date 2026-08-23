"""Continuous-integration classification and cross-repository issue lifecycle."""

from .support import *


def test_ci_billing_tolerance():
    """shepherd's CI gate: a GitHub Actions run that could not EXECUTE (billing / spending-limit /
    startup failure) is NOT a real failure and must never block a PR/merge — only a check that ran
    and FAILED blocks. classify_ci_checks splits the rollup into (real, nonrun, pending)."""
    print("test_ci_billing_tolerance")
    rollup = [
        {"name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": "SKIPPED"},
        {
            "name": "e2e",
            "status": "COMPLETED",
            "conclusion": "STARTUP_FAILURE",
            "detailsUrl": "https://github.com/o/r/actions/runs/1",
        },
        {"name": "build", "status": "COMPLETED", "conclusion": "ACTION_REQUIRED"},
        {
            "name": "billing-service-tests",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
        {"name": "integration", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "deploy-preview", "status": "IN_PROGRESS", "conclusion": ""},
        {"context": "legacy/ci", "state": "SUCCESS"},
        {"context": "legacy/fail", "state": "ERROR"},
        "garbage",
        None,
        42,
    ]
    (real, nonrun, pending) = m.classify_ci_checks(rollup)
    check(
        set(real) == {"integration", "legacy/fail", "billing-service-tests"},
        "checks that RAN and failed block — incl. a money-NAMED check (FAILURE conclusion) that ran and failed",
    )
    check(
        set(nonrun) == {"e2e", "build"},
        "non-run conclusions (startup-failure / billing action-required) are non-blocking (could-not-run)",
    )
    check(
        set(pending) == {"deploy-preview"},
        "in-progress check is 'pending', not a failure",
    )
    check(
        m.classify_ci_checks([]) == ([], [], [])
        and m.classify_ci_checks(None) == ([], [], []),
        "empty/None rollup → no blockers, no crash",
    )
    green = [
        {"name": "t", "conclusion": "SUCCESS"},
        {"name": "b", "conclusion": "STARTUP_FAILURE"},
    ]
    (r2, n2, p2) = m.classify_ci_checks(green)
    check(
        r2 == [] and set(n2) == {"b"} and (p2 == []),
        "a PR blocked ONLY by a billing/non-run check has zero real failures → mergeable",
    )


def test_shepherd_empty_rollup():
    """shepherd's empty-rollup gate: a PR whose CI has not registered yet (statusCheckRollup=[] with a
    not-yet-reported mergeState like UNKNOWN) is NOT green — report 'waiting' and never merge, even with
    --merge. An empty rollup with mergeState=CLEAN (no CI configured) is legitimately 'ready'."""
    print("test_shepherd_empty_rollup")
    import io, contextlib

    saved_cwd = os.getcwd()
    saved_path = os.environ.get("PATH", "")
    saved_remote = m.has_remote
    with tempfile.TemporaryDirectory() as st:
        repo = os.path.join(st, "repo")
        os.makedirs(repo)

        def g(*a):
            subprocess.run(["git"] + list(a), cwd=repo, capture_output=True, text=True)

        g("-c", "init.defaultBranch=main", "init", "-q")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        g("symbolic-ref", "HEAD", "refs/heads/main")
        with open(os.path.join(repo, "f"), "w") as f:
            f.write("1")
        g("add", ".")
        g("commit", "-qm", "base")
        g("checkout", "-q", "-b", "feature")
        with open(os.path.join(repo, "f"), "w") as f:
            f.write("2")
        g("add", ".")
        g("commit", "-qm", "work")
        ghdir = os.path.join(st, "bin")
        os.makedirs(ghdir)
        ghp = os.path.join(ghdir, "gh")
        with open(ghp, "w") as f:
            f.write(
                '#!/bin/bash\nif [ "$1 $2" = "pr view" ]; then echo "$NEOMAX_TEST_GH_JSON"; exit 0; fi\necho merged; exit 0\n'
            )
        os.chmod(ghp, 493)
        os.environ["PATH"] = ghdir + os.pathsep + saved_path
        m.has_remote = lambda r: True
        os.chdir(repo)
        try:

            def shep(js, merge=False):
                os.environ["NEOMAX_TEST_GH_JSON"] = js
                argv = ["--branch", "feature", "--base", "main"] + (
                    ["--merge"] if merge else []
                )
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    m.cmd_shepherd(argv)
                return buf.getvalue().strip()

            out = shep(
                '{"state":"OPEN","url":"u","mergeStateStatus":"UNKNOWN","statusCheckRollup":[]}',
                merge=True,
            )
            check(
                out.startswith("waiting: CI not reported yet"),
                "empty rollup + UNKNOWN mergeState → 'waiting' (CI not registered), never merged even with --merge",
            )
            out = shep(
                '{"state":"OPEN","url":"u","mergeStateStatus":"CLEAN","statusCheckRollup":[]}'
            )
            check(
                out.startswith("ready"),
                "empty rollup + CLEAN mergeState (no CI configured) → 'ready' (legitimately mergeable)",
            )
        finally:
            os.chdir(saved_cwd)
            m.has_remote = saved_remote
            os.environ.pop("NEOMAX_TEST_GH_JSON", None)
            os.environ["PATH"] = saved_path


def test_issue_pipeline():
    """Cross-repo issue ledger — the /find-issues + /fix-issues spine. Registry-driven repo
    discovery, gh-optional mirrors (local-only fallback), durable no-double-fix claim, link/brief,
    and ci-sync (dry/apply/idempotent/hand-edit guard). Fully hermetic — overrides the module's
    state-dir globals so nothing touches real ~/.neomax, and stubs gh."""
    print("test_issue_pipeline")
    saved = {
        k: getattr(m, k)
        for k in ("STATE_DIR", "ISSUES_DIR", "EVENTS_DIR", "PROJECTS_FILE", "GH_BIN")
    }
    saved_sess = os.environ.get("NEOMAX_ORCH_SESSION")
    (saved_remote, saved_live) = (m.has_remote, m._session_live)
    with tempfile.TemporaryDirectory() as st:
        home = os.path.join(st, "home")
        os.makedirs(home)
        m.STATE_DIR = home
        m.ISSUES_DIR = os.path.join(home, "issues")
        m.EVENTS_DIR = os.path.join(home, "events")
        m.PROJECTS_FILE = os.path.join(home, "projects.json")
        root = os.path.join(st, "proj")
        repos = ["repoA", "repoB", "repoC"]
        for r in repos:
            os.makedirs(os.path.join(root, r))
        json.dump(
            {
                "demo": {
                    "root": root,
                    "repos": repos,
                    "branch_prefix": "demo",
                    "brain": "CLAUDE.md",
                    "planning": "docs",
                }
            },
            open(m.PROJECTS_FILE, "w"),
        )
        try:
            check(
                [n for (n, _) in m.project_repo_paths("demo")] == repos,
                "project_repo_paths reads the repo set from the registry (no hardcoding)",
            )
            m.GH_BIN = "/nonexistent-gh-xyz"
            iss = m.open_cross_repo_issue(
                "Null deref spanning gateway+site", "the body", "demo", None
            )
            check(
                set(iss["repos"]) == set(repos)
                and all((e["state"] == "local" for e in iss["repos"].values())),
                "issue open with no gh → local-only mirror in EVERY project repo (cross-repo by default)",
            )
            check(
                iss["status"] == "open"
                and iss["key"].startswith("iss-")
                and (m.load_issue(iss["key"]) is not None)
                and (len(m.list_issues(project="demo")) == 1),
                "new issue is open with a canonical key, persisted, and listed",
            )
            check(
                iss.get("_new") is True and iss.get("fingerprint"),
                "a freshly created issue carries _new=True and a fingerprint",
            )
            a = m.open_cross_repo_issue("Race in proxy worker pool", "x", "demo", None)
            b = m.open_cross_repo_issue(
                "race  in   PROXY  worker POOL!!", "y", "demo", None
            )
            check(
                b["key"] == a["key"] and b.get("_new") is False,
                "re-filing a still-open issue (fuzzy title match) DEDUPES — same key, no duplicate",
            )
            check(
                os.path.exists(os.path.join(m.ISSUES_DIR, ".open.lock")),
                "the dedup→create path is flock-guarded (.open.lock) so concurrent finds can't double-file",
            )
            c = m.open_cross_repo_issue(
                "Race in proxy worker pool", "z", "demo", None, force_new=True
            )
            check(
                c["key"] != a["key"] and c.get("_new") is True,
                "--force-new overrides dedupe (a genuinely separate issue)",
            )
            for k in (a["key"], c["key"]):
                d = m.load_issue(k)
                d["status"] = "done"
                m.save_issue(d)
            e = m.open_cross_repo_issue("Race in proxy worker pool", "w", "demo", None)
            check(
                e.get("_new") is True and e["key"] not in (a["key"], c["key"]),
                "a DONE issue does not block re-filing the same fingerprint (recurrence allowed)",
            )
            for k in {a["key"], c["key"], e["key"]}:
                p = os.path.join(m.ISSUES_DIR, k + ".json")
                if os.path.exists(p):
                    os.remove(p)
            os.environ["NEOMAX_ORCH_SESSION"] = "sessA"
            got = m.claim_issue(iss["key"], take=True)
            check(
                got and got["status"] == "claimed",
                "claim takes an open issue (→ claimed)",
            )
            os.environ["NEOMAX_ORCH_SESSION"] = "sessB"
            m._session_live = lambda s: True
            check(
                m.claim_issue(iss["key"], take=True) is None,
                "a 2nd LIVE session cannot steal an already-claimed issue (no double-fix)",
            )
            m._session_live = lambda s: False
            check(
                m.claim_issue(iss["key"], take=True) is not None,
                "a DEAD holder's claim is reclaimable",
            )
            os.environ["NEOMAX_ORCH_SESSION"] = "sessA"
            i2 = m.load_issue(iss["key"])
            i2["runs"].append("run-1")
            i2["prs"]["repoA"] = "http://pr/1"
            m.save_issue(i2)
            brief = m._issue_brief(i2)
            check(
                iss["key"] in brief and "repoA" in brief and ("repoB" in brief),
                "fix brief names the issue + every affected repo (cross-repo)",
            )
            dry = dict(m.ci_sync("demo", apply=False))
            check(
                set(dry) == set(repos)
                and all((v.startswith("would-") for v in dry.values())),
                "ci-sync dry run reports an action per repo, writes nothing",
            )
            ap = dict(m.ci_sync("demo", apply=True))
            wf = os.path.join(root, "repoA", ".github", "workflows", "neomax-ci.yml")
            check(
                all((v == "created" for v in ap.values())) and os.path.isfile(wf),
                "ci-sync --apply installs the workflow into every repo",
            )
            check(
                all(
                    (
                        v == "unchanged"
                        for v in dict(m.ci_sync("demo", apply=True)).values()
                    )
                ),
                "ci-sync is idempotent",
            )
            with open(wf, "w") as f:
                f.write("name: my-own-ci\non: push\njobs: {}\n")
            check(
                dict(m.ci_sync("demo", apply=True))["repoA"].startswith("skip"),
                "ci-sync won't clobber a foreign workflow without --force",
            )
            check(
                dict(m.ci_sync("demo", apply=True, force=True))["repoA"] == "updated",
                "ci-sync --force overwrites a foreign workflow",
            )
            ghp = os.path.join(st, "gh")
            with open(ghp, "w") as f:
                f.write(STUB_GH)
            os.chmod(ghp, 493)
            m.GH_BIN = ghp
            m.has_remote = lambda repo: True
            iss2 = m.open_cross_repo_issue(
                "Cross-repo contract drift", "b", "demo", ["repoA", "repoB"]
            )
            check(
                set(iss2["repos"]) == {"repoA", "repoB"},
                "issue open --repos limits to the named repos",
            )
            try:
                m.open_cross_repo_issue("Typo target", "b", "demo", ["nope-typo"])
                raised = False
            except ValueError:
                raised = True
            check(
                raised,
                "issue open --repos with only unknown repo names RAISES (no silent fan-out to all)",
            )
            try:
                m.open_cross_repo_issue(
                    "Partial typo", "b", "demo", ["repoA", "repoB-typo"]
                )
                raised_partial = False
            except ValueError as e:
                raised_partial = "repoB-typo" in str(e)
            check(
                raised_partial,
                "issue open --repos with ANY unknown name RAISES (no silent partial drop)",
            )
            iss_sub = m.open_cross_repo_issue("Valid subset", "b", "demo", ["repoA"])
            check(
                set(iss_sub["repos"]) == {"repoA"},
                "issue open --repos with a fully-valid subset still works",
            )
            iss_all = m.open_cross_repo_issue("All-repo issue", "b", "demo", None)
            check(
                set(iss_all["repos"]) == set(repos),
                "issue open with None repos still targets every project repo",
            )
            check(
                all(
                    (
                        str(iss2["repos"][r]["url"]).startswith("http")
                        and iss2["repos"][r]["number"]
                        for r in ("repoA", "repoB")
                    )
                ),
                "with gh available each mirror records a real issue URL + number",
            )
            iss2_before = m.load_issue(iss2["key"])["updated"]
            m.reconcile_issues("demo")
            check(
                m.load_issue(iss2["key"])["updated"] == iss2_before,
                "reconcile_issues does NOT bump `updated` when no mirror state changed",
            )
            m.close_cross_repo_issue(iss2, comment="done")
            check(
                m.load_issue(iss2["key"])["status"] == "done",
                "close_cross_repo_issue closes the mirrors and marks the ledger record done",
            )
        finally:
            for k, v in saved.items():
                setattr(m, k, v)
            (m.has_remote, m._session_live) = (saved_remote, saved_live)
            if saved_sess is None:
                os.environ.pop("NEOMAX_ORCH_SESSION", None)
            else:
                os.environ["NEOMAX_ORCH_SESSION"] = saved_sess


def test_issue_batch_claim():
    """/fix-issues drains a BATCH (not one-at-a-time): `issue next --all` claims EVERY open unclaimed
    issue at once, `--batch N` caps it, default returns a single object (back-compat). `release`
    returns an issue to the pool, and a crashed loop's stale claim is reclaimable."""
    print("test_issue_batch_claim")
    import io, contextlib

    saved = {
        k: getattr(m, k)
        for k in ("STATE_DIR", "ISSUES_DIR", "EVENTS_DIR", "PROJECTS_FILE", "GH_BIN")
    }
    saved_sess = os.environ.get("NEOMAX_ORCH_SESSION")
    with tempfile.TemporaryDirectory() as st:
        home = os.path.join(st, "home")
        os.makedirs(home)
        m.STATE_DIR = home
        m.ISSUES_DIR = os.path.join(home, "issues")
        m.EVENTS_DIR = os.path.join(home, "events")
        m.PROJECTS_FILE = os.path.join(home, "projects.json")
        m.GH_BIN = "/nonexistent-gh"
        root = os.path.join(st, "proj")
        repos = ["r1", "r2"]
        for r in repos:
            os.makedirs(os.path.join(root, r))
        json.dump(
            {
                "demo": {
                    "root": root,
                    "repos": repos,
                    "branch_prefix": "d",
                    "brain": "CLAUDE.md",
                    "planning": "docs",
                }
            },
            open(m.PROJECTS_FILE, "w"),
        )
        os.environ["NEOMAX_ORCH_SESSION"] = "sessX"

        def run_next(*args):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    m.cmd_issue(["next", "--json", "--project", "demo"] + list(args))
            except SystemExit:
                pass
            return json.loads(buf.getvalue() or "null")

        saved_live0 = m._session_live
        m._session_live = lambda s: True
        try:
            keys = [
                m.open_cross_repo_issue(
                    "distinct issue number %d" % i, "b", "demo", None
                )["key"]
                for i in range(5)
            ]
            check(
                len(m.list_issues(project="demo", status="open")) == 5,
                "5 distinct open issues filed",
            )
            two = run_next("--batch", "2")
            check(
                isinstance(two, list) and len(two) == 2,
                "issue next --batch 2 claims exactly 2 at once",
            )
            rest = run_next("--all")
            check(
                isinstance(rest, list) and len(rest) == 3,
                "issue next --all claims ALL remaining open issues in one pass",
            )
            check(
                run_next("--all") == [],
                "nothing left → empty batch (no double-fix across passes)",
            )
            check(
                len(m.list_issues(project="demo", status="open")) == 0,
                "all 5 issues claimed (none left open)",
            )
            one = run_next()
            check(
                one is None,
                "default issue next returns a single object/null (back-compat), here null",
            )
            m.claim_issue(keys[0], take=False)
            check(
                m.load_issue(keys[0])["status"] == "open",
                "release returns a claimed issue to 'open'",
            )
            back = run_next()
            check(
                isinstance(back, dict) and back.get("key") == keys[0],
                "a released issue is re-claimed by the next pass",
            )
            i1 = m.load_issue(keys[1])
            i1["status"] = "fixing"
            i1["claim"] = {"session": "deadSess", "pid": 999999, "ts": 1}
            m.save_issue(i1)
            saved_live = m._session_live
            m._session_live = lambda s: False
            try:
                got = run_next("--all")
                check(
                    any((x["key"] == keys[1] for x in got)),
                    "an issue stranded by a CRASHED fix loop (stale claim) is reclaimed by next --all",
                )
            finally:
                m._session_live = saved_live
        finally:
            m._session_live = saved_live0
            for k, v in saved.items():
                setattr(m, k, v)
            if saved_sess is None:
                os.environ.pop("NEOMAX_ORCH_SESSION", None)
            else:
                os.environ["NEOMAX_ORCH_SESSION"] = saved_sess
