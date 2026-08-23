"""Git worktrees, pull requests, and change outcomes."""


def git(args, cwd, check=False):
    from . import config as _config

    try:
        r = _config.subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True
        )
    except (FileNotFoundError, NotADirectoryError):
        if check:
            raise RuntimeError("git %s failed: cwd %s is gone" % (" ".join(args), cwd))
        return _config.subprocess.CompletedProcess(
            ["git"] + args, 1, "", "cwd %s missing" % cwd
        )
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r


def has_remote(repo):
    return bool(git(["remote"], repo).stdout.strip())


def default_branch(repo):
    r = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split("/", 1)[-1]
    for cand in ("main", "master"):
        if git(["rev-parse", "--verify", "origin/" + cand], repo).returncode == 0:
            return cand
    return "main"


def existing_pr(repo, branch):
    """Return {url,state} for an existing PR on this head branch, or None. Idempotency
    probe: survives total local-state loss (queries GitHub, not our registry)."""
    from . import config as _config

    if not _config.shutil.which("gh"):
        return None
    r = _config.subprocess.run(
        [
            "gh",
            "pr",
            "view",
            branch,
            "--json",
            "url,state,number",
            "--jq",
            "{url:.url,state:.state,number:.number}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return _config.json.loads(r.stdout)
    except ValueError:
        return None


def open_pr(rec, base=None, draft=True):
    """Push a run's branch and open a GitHub PR via gh. Returns the PR URL or None.
    Idempotent: if a PR already exists for this branch it's surfaced (never duplicated),
    and a branch already merged into base opens no PR. Safe no-op with a clear message
    if there's no remote or gh isn't available."""
    from . import config as _config
    from . import models as _models
    from . import runs as _runs

    (repo, branch) = (rec.get("repo"), rec.get("branch"))
    if not repo or not branch:
        _models.err(
            "neomax: no repo/branch for run %s — cannot open a PR" % rec.get("id")
        )
        return None
    if not _config.shutil.which("gh"):
        _models.err(
            "neomax: gh CLI not found — push the branch and open the PR manually"
        )
        return None
    if not has_remote(repo):
        _models.err(
            "neomax: repo has no git remote — cannot open a PR (work is safe on branch %s)"
            % branch
        )
        return None
    base = base or default_branch(repo)
    pre = existing_pr(repo, branch)
    if pre and pre.get("url"):
        rec["pr_url"] = pre["url"]
        _runs.save_run(rec)
        _models.err(
            "NEOMAX PR url=%s base=%s (already exists, state=%s)"
            % (pre["url"], base, pre.get("state"))
        )
        return pre["url"]
    if git(
        ["merge-base", "--is-ancestor", branch, "origin/" + base], repo
    ).returncode == 0 and git(
        ["rev-list", "--count", "origin/%s..%s" % (base, branch)], repo
    ).stdout.strip() in ("", "0"):
        _models.err(
            "neomax: branch %s already merged into %s — no PR needed" % (branch, base)
        )
        return None
    push = git(["push", "-u", "origin", branch], repo)
    if push.returncode != 0:
        _models.err(
            "neomax: git push failed (%s) — branch kept locally, no PR opened"
            % push.stderr.strip()[:200]
        )
        return None
    title = (rec.get("prompt") or branch).strip().splitlines()[0][:72]
    receipt = "<!-- neomax:run:%s base:%s -->" % (rec["id"], base)
    body = (
        (rec.get("result_text") or "").strip()
        + "\n\n---\n🤖 Delegated worker run `%s` (account %s, branch `%s`).\nReview the diff before merging.\n\n%s"
        % (rec["id"], rec.get("profile", "?").split("/")[-1], branch, receipt)
    )
    cmd = [
        "gh",
        "pr",
        "create",
        "--head",
        branch,
        "--base",
        base,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        cmd.append("--draft")
    r = _config.subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        again = existing_pr(repo, branch)
        url = again.get("url") if again else None
        if not url:
            _models.err("neomax: gh pr create failed: %s" % r.stderr.strip()[:300])
            return None
    else:
        url = r.stdout.strip()
    rec["pr_url"] = url
    _runs.save_run(rec)
    _runs.log_event(rec, "pr_opened", url=url, base=base)
    _models.err("NEOMAX PR url=%s base=%s" % (url, base))
    return url


class NotIsolable(Exception):
    """Worktree isolation was requested but impossible — fail closed."""


def make_worktree(runid, base_ref=None):
    """Returns (workdir, repo, branch, base). Raises NotIsolable rather than
    silently running in the live checkout when isolation can't be provided.
    base_ref pins the branch point (e.g. an integration branch) instead of HEAD."""
    from . import config as _config
    from . import models as _models

    cwd = _config.os.getcwd()
    r = git(["rev-parse", "--show-toplevel"], cwd)
    if r.returncode != 0:
        return (cwd, None, None, None)
    repo = r.stdout.strip()
    ref = base_ref or "HEAD"
    r = git(["rev-parse", ref], repo)
    if r.returncode != 0:
        if base_ref:
            raise NotIsolable("base ref %r does not exist in this repo" % base_ref)
        raise NotIsolable(
            "repo has no commits yet — make an initial commit first, or pass --no-worktree to run in the live checkout deliberately"
        )
    base = r.stdout.strip()
    branch = "neomax/" + runid
    _config.os.makedirs(_config.WORKTREES_DIR, exist_ok=True)
    wt = _config.os.path.join(
        _config.WORKTREES_DIR, "%s-%s" % (_config.os.path.basename(repo), runid)
    )
    r = git(["worktree", "add", "-b", branch, wt, base], repo)
    if r.returncode != 0:
        raise NotIsolable("git worktree add failed: %s" % r.stderr.strip())
    _models.err(
        "neomax → isolated worktree: %s (branch %s, base %s)" % (wt, branch, base[:8])
    )
    setup = _config.os.path.join(repo, ".neomax-setup.sh")
    if _config.os.path.isfile(setup):
        _models.err("neomax: running repo setup hook in worktree...")
        s = _config.subprocess.run(
            ["bash", setup], cwd=wt, capture_output=True, text=True, timeout=600
        )
        if s.returncode != 0:
            _models.err(
                "neomax: setup hook FAILED (rc=%d): %s"
                % (s.returncode, s.stderr[-300:])
            )
    return (wt, repo, branch, base)


def worktree_changes(rec):
    """(dirty_bool, commits_ahead_int) for a run's worktree, or (False, 0) if no
    worktree / dir is gone. The single source of truth for 'has work?' — consumed by
    `clean` to decide whether deleting a branch would destroy unmerged work.

    FAILS SAFE: if EITHER git query errors (non-zero rc) we return (True, 1) = "unknown,
    assume work present" rather than trusting empty stdout. A failed `rev-list base..branch`
    (e.g. the recorded base SHA became unreachable after a parent-repo rebase/gc, or base was
    an ephemeral integration ref since deleted) exits 128 with EMPTY stdout — the old
    `int("" or "0")` read that as 0-ahead, so a clean tree looked like 'no work' and `clean`
    (without --force) would `git branch -D` real committed work. Never again: unverifiable =
    protected. --force still bypasses this check entirely (that's what --force is for)."""
    from . import config as _config

    (repo, wt, branch, base) = (
        rec.get("repo"),
        rec.get("worktree"),
        rec.get("branch"),
        rec.get("base"),
    )
    if (
        not wt
        or not _config.os.path.isdir(wt)
        or (not repo)
        or (not _config.os.path.isdir(repo))
    ):
        return (False, 0)
    st = git(["status", "--porcelain"], wt)
    rl = git(["rev-list", "--count", "%s..%s" % (base, branch)], repo)
    if st.returncode != 0 or rl.returncode != 0:
        return (True, 1)
    dirty = bool(st.stdout.strip())
    try:
        ahead = int(rl.stdout.strip() or "0")
    except ValueError:
        return (True, 1)
    return (dirty, ahead)


def worktree_outcome(rec):
    """After a run: clean up an unchanged worktree, or report what's on the branch."""
    from . import config as _config
    from . import models as _models
    from . import runs as _runs

    (repo, wt, branch, base) = (
        rec.get("repo"),
        rec.get("worktree"),
        rec.get("branch"),
        rec.get("base"),
    )
    if not wt:
        return
    if not _config.os.path.isdir(wt):
        rec["worktree_state"] = "vanished"
        _models.err(
            "neomax: WARNING worktree %s no longer exists — uncommitted work (if any) is lost; committed work survives on branch %s"
            % (wt, branch)
        )
        return
    (dirty, ahead) = worktree_changes(rec)
    if not dirty and ahead == 0:
        killed = rec.get("killed")
        if not killed:
            try:
                with open(_runs.run_path(rec["id"])) as f:
                    killed = bool(_config.json.load(f).get("killed"))
            except (OSError, ValueError, KeyError):
                killed = False
        if rec["status"] in ("done", "error") and (not killed):
            git(["worktree", "remove", "--force", wt], repo)
            git(["branch", "-D", branch], repo)
            rec["worktree_state"] = "cleaned"
            _models.err("neomax: worker made no changes; worktree cleaned up")
        else:
            rec["worktree_state"] = "empty_kept"
    else:
        rec["worktree_state"] = "has_changes"
        touched = set()
        for f in git(
            ["diff", "--name-only", "%s..%s" % (base, branch)], repo
        ).stdout.split():
            touched.add(f)
        for line in git(["status", "--porcelain"], wt).stdout.splitlines():
            name = line[3:].strip().split(" -> ")[-1]
            if name:
                touched.add(name)
        rec["files_touched"] = sorted(touched)
        _models.err(
            "NEOMAX RESULT branch=%s worktree=%s commits_ahead=%d uncommitted=%s"
            % (branch, wt, ahead, "yes" if dirty else "no")
        )
        _models.err("neomax: review & merge, then: neomax clean %s" % rec["id"])
