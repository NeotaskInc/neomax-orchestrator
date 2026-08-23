"""Portable project registration, coordinated worktrees, and durable task backlog."""

from .support import *


def test_project_registration():
    """Launch directories auto-register while explicit project configuration remains supported."""
    print("test_project_registration")
    import io, contextlib

    saved = (m.STATE_DIR, m.PROJECTS_FILE, m.HOME)
    saved_cwd = os.getcwd()
    saved_env = {
        k: os.environ.get(k)
        for k in ("NEOMAX_ROLE", "NEOMAX_MODE", "NEOMAX_FLEET", "NEOMAX_PROJECT_ROOT")
    }
    with tempfile.TemporaryDirectory() as td:
        td = os.path.realpath(td)
        home = os.path.join(td, "home")
        proj = os.path.join(home, "DebateX")
        os.makedirs(proj)
        os.makedirs(os.path.join(home, "Downloads"))
        m.STATE_DIR = td
        m.PROJECTS_FILE = os.path.join(td, "projects.json")
        m.HOME = home
        try:
            os.environ["NEOMAX_ROLE"] = "claude"
            os.environ.pop("NEOMAX_MODE", None)
            os.environ["NEOMAX_FLEET"] = "all"
            check(
                m.is_unregistered_project_dir(proj),
                "a new directory is available for project setup",
            )
            check(
                not m.is_unregistered_project_dir(home),
                "the home directory is not registered as one project",
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                m.cmd_project_register(
                    [
                        "--name",
                        "DebateX",
                        "--root",
                        proj,
                        "--prefix",
                        "dbx",
                        "--desc",
                        "DebateX — a debate trainer",
                    ]
                )
            check(
                "registered" in buf.getvalue() and "project=debatex" in buf.getvalue(),
                "project-register confirms registration + the project= tag",
            )
            check(
                m.project_of(proj) == "debatex"
                and m.project_of(os.path.join(proj, "src")) == "debatex",
                "project_of resolves the new project (and paths under it)",
            )
            check(
                set(m.load_projects()) == {"debatex"},
                "tracked runtime adds no built-in projects to the local registry",
            )
            check(
                not m.is_unregistered_project_dir(proj),
                "a registered dir no longer triggers onboarding",
            )
            (ok, _) = m.register_project("debatex", {"root": proj})
            check(not ok, "duplicate name refused without --force")
            (ok, _) = m.register_project("other", {"root": os.path.join(proj, "sub")})
            check(
                not ok,
                "a root nested under an existing project is refused (ambiguous tagging)",
            )
            fresh = os.path.join(home, "FreshThing")
            os.makedirs(fresh)
            os.chdir(fresh)
            os.environ["NEOMAX_PROJECT_ROOT"] = fresh
            d_new = m.orient_directive()
            check(
                m.project_of(fresh) == "freshthing" and "NEW PROJECT:" in d_new,
                "orient registers the launch directory and keeps generic project setup available",
            )
            check(
                "project-register --name freshthing" in d_new
                and "must not block" in d_new,
                "new-project setup can refine the registry without unnecessarily blocking work",
            )
            check(
                m.load_projects()["freshthing"]["repos"] == ["."],
                "an ordinary project defaults to a portable single-repo definition",
            )
            os.chdir(proj)
            os.environ["NEOMAX_PROJECT_ROOT"] = proj
            d_reg = m.orient_directive()
            check("NEW PROJECT SETUP" not in d_reg, "registered project opens directly")
            od = os.path.join(proj, "docs", "neomax-orchestrator")
            os.makedirs(od)
            open(os.path.join(od, "ORCHESTRATOR_OPENER.md"), "w").write(
                "DEBATEX-CUSTOM-OPENER-LINE"
            )
            check(
                "DEBATEX-CUSTOM-OPENER-LINE" in m.orient_directive(),
                "a registered project's ORCHESTRATOR_OPENER.md is appended to the live opener",
            )
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                m.cmd_project_register(["--unregister", "debatex"])
            check(
                "removed 'debatex'" in buf2.getvalue() and m.project_of(proj) is None,
                "project-unregister removes a custom project",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                m.cmd_project_register(["--unregister", "freshthing"])
            check(
                m.load_projects() == {},
                "all locally registered projects can be removed",
            )
        finally:
            os.chdir(saved_cwd)
            (m.STATE_DIR, m.PROJECTS_FILE, m.HOME) = saved
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_portable_project_worktrees():
    """The coordinated-worktree helper resolves only the local project registry."""
    print("test_portable_project_worktrees")
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "workspace")
        state = os.path.join(td, "state")
        os.makedirs(root)
        os.makedirs(state)
        for repo_name in ("service-a", "service-b"):
            repo = os.path.join(root, repo_name)
            os.makedirs(repo)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write(repo_name + "\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
        with open(os.path.join(state, "projects.json"), "w") as f:
            json.dump(
                {
                    "sample": {
                        "root": root,
                        "repos": ["service-a", "service-b"],
                        "branch_prefix": "samp",
                    }
                },
                f,
            )
        env = dict(os.environ, NEOMAX_HOME=state)
        helper = os.path.join(os.path.dirname(HERE), "project", "neomax-worktrees")
        made = subprocess.run(
            [helper, "feature"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        set_dir = os.path.join(state, "coordinated-worktrees", "sample", "feature")
        check(
            made.returncode == 0
            and os.path.isdir(os.path.join(set_dir, "service-a"))
            and os.path.isdir(os.path.join(set_dir, "service-b")),
            "neomax-worktrees creates a coordinated set from generic registry repositories",
        )
        branches = [
            subprocess.run(
                ["git", "-C", os.path.join(set_dir, name), "branch", "--show-current"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            for name in ("service-a", "service-b")
        ]
        check(
            branches == ["samp/feature", "samp/feature"],
            "coordinated branches use the registered project prefix",
        )
        removed = subprocess.run(
            [helper, "--remove", "feature"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        check(
            removed.returncode == 0 and (not os.path.exists(set_dir)),
            "neomax-worktrees removes a clean coordinated set",
        )


def test_task_backlog():
    """Durable backlog: task add/update/remove + the cmd_task CLI (auto project-tag from cwd,
    status shortcuts, done-tasks drop from the open list, notes/run-links persist, --json)."""
    print("test_task_backlog")
    import io, contextlib

    (saved_state, saved_tasks, saved_proj) = (m.STATE_DIR, m.TASKS_FILE, m.project_of)
    with tempfile.TemporaryDirectory() as st:
        m.STATE_DIR = st
        m.TASKS_FILE = os.path.join(st, "tasks.json")
        m.project_of = lambda p=None: "alpha"
        try:
            t1 = m.task_add("fix auth bug", project="alpha")
            t2 = m.task_add("restyle modal", project="alpha", note="css only")
            t3 = m.task_add("deploy site", project="beta")
            check(
                t1 == "t1" and t2 == "t2" and (t3 == "t3"),
                "task_add returns sequential ids",
            )
            check(
                len(m.tasks_for_project("alpha")) == 2
                and len(m.tasks_for_project("beta")) == 1,
                "tasks_for_project filters by project",
            )
            m.task_update(t1, status="doing")
            m.task_update(t2, status="done")
            check(
                [t["id"] for t in m.tasks_for_project("alpha")] == ["t1"],
                "a DONE task drops out of the open list (no re-doing resolved work)",
            )
            check(
                len(m.tasks_for_project("alpha", include_done=True)) == 2,
                "include_done shows it again",
            )
            m.task_update(t1, note="found it in SettingsModal.tsx", run="20260613-x")
            rec = m.load_tasks()["tasks"]["t1"]
            check("found it in SettingsModal.tsx" in rec["notes"], "a note persists")
            check(rec["runs"] == ["20260613-x"], "a linked run id persists")
            check(
                m.task_remove(t3) and "t3" not in m.load_tasks()["tasks"],
                "task_remove deletes",
            )

            def run_task(args):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    m.cmd_task(args)
                return buf.getvalue()

            check(
                "added [alpha]" in run_task(["add", "new", "thing"]),
                "cmd_task add auto-tags the current project + joins multi-word titles",
            )
            rows = json.loads(run_task(["list", "--json", "--all-projects"]))
            check(
                any(
                    (
                        r["title"] == "new thing" and r["project"] == "alpha"
                        for r in rows
                    )
                ),
                "cmd_task list --json returns the tasks",
            )
            check(
                "t1 → done" in run_task(["done", "t1"])
                and m.load_tasks()["tasks"]["t1"]["status"] == "done",
                "cmd_task done <id> shortcut moves the status",
            )
            eb = io.StringIO()
            with contextlib.redirect_stderr(eb):
                m.cmd_task(["done", "t999"])
            check(
                "no task t999" in eb.getvalue(),
                "an unknown id reports cleanly (no crash)",
            )
        finally:
            (m.STATE_DIR, m.TASKS_FILE, m.project_of) = (
                saved_state,
                saved_tasks,
                saved_proj,
            )
