"""Run listing, logs, merge detection, and cleanup."""


def cmd_ls(argv):
    from . import config as _config
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    hook_mode = "--hook" in argv
    if hook_mode and _config.os.environ.get("NEOMAX_WORKER"):
        return
    rows = []
    has_orphan = False
    inbox = 0
    for rec in _runs.all_runs():
        st = _orchestrators.effective_status(rec)
        if st == "orphaned":
            has_orphan = True
        unacked = _orchestrators.in_inbox(rec)
        if unacked:
            inbox += 1
        unfinished = (
            st in ("running", "orphaned")
            or rec.get("worktree_state") == "has_changes"
            or unacked
        )
        if hook_mode and (not unfinished):
            continue
        age_min = int((_config.time.time() - rec.get("started", 0)) / 60)
        eng = rec.get("engine", "claude")
        children = rec.get("children") or []
        child_note = " +%d sub" % len(children) if children else ""
        owner_mark = "»OTHER " if _orchestrators.owned_by_other_live_orch(rec) else ""
        rows.append(
            (
                owner_mark + rec["id"],
                st,
                "%s/%s" % (eng[:3], rec.get("profile", "?").split("/")[-1]),
                "%dm" % age_min,
                (rec.get("branch") or "-") + child_note,
                (rec.get("prompt") or "")[:54].replace("\n", " "),
            )
        )
    if hook_mode:
        if not rows:
            return
        if inbox:
            print(
                "[neomax] COMPLETION INBOX: %d delegated run(s) FINISHED and are awaiting you — review their result + resolve them, then `neomax ack <id>` (or `clean <id>` once merged). Run `neomax reconcile` to see exactly what each needs. Nothing finished is ever dropped: every run stays here until you acknowledge it."
                % inbox
            )
        else:
            print(
                "[neomax] Unfinished delegated runs exist — you are coordinating multi-account workers (Claude + Codex + OpenCode + Kimi). `neomax reconcile` for the full picture; resume/retry/kill/clean as needed."
            )
        if has_orphan:
            print(
                "[neomax] WARNING a run is 'orphaned' (worker still alive after its engine died). Do NOT resume/retry/clean it — `neomax kill <id>` first."
            )
    if not rows:
        print("no neomax runs recorded")
        return
    print(
        "%-29s %-12s %-16s %-6s %-30s %s"
        % ("RUN", "STATUS", "ENG/ACCT", "AGE", "BRANCH", "PROMPT")
    )
    for r in rows:
        print("%-29s %-12s %-16s %-6s %-30s %s" % r)


def cmd_log(argv):
    from . import config as _config
    from . import models as _models
    from . import premerge as _premerge
    from . import runs as _runs

    if not argv:
        _premerge.usage()
    rec = _runs.load_run(argv[0])
    if not rec or not rec.get("log"):
        _models.err("neomax: no log for %s" % argv[0])
        _config.sys.exit(1)
    with open(rec["log"], "rb") as f:
        for raw in f.read().decode("utf-8", "replace").splitlines():
            try:
                evt = _config.json.loads(raw)
            except ValueError:
                continue
            t = evt.get("type")
            if t == "assistant":
                for blk in (evt.get("message") or {}).get("content") or []:
                    if blk.get("type") == "text" and blk.get("text", "").strip():
                        print("· " + blk["text"][:200])
                    elif blk.get("type") == "tool_use":
                        print(
                            "⚙ %s %s"
                            % (
                                blk.get("name"),
                                _config.json.dumps(blk.get("input", {}))[:140],
                            )
                        )
            elif t == "result":
                print(
                    "== result (%s) ==\n%s"
                    % (evt.get("subtype"), (evt.get("result") or "")[:2000])
                )


def remove_logs(rec):
    from . import config as _config

    log = rec.get("log")
    if not log:
        return
    base = log.rsplit(".attempt", 1)[0]
    for name in (
        _config.os.listdir(_config.LOGS_DIR)
        if _config.os.path.isdir(_config.LOGS_DIR)
        else []
    ):
        if name.startswith(_config.os.path.basename(base)):
            try:
                _config.os.remove(_config.os.path.join(_config.LOGS_DIR, name))
            except OSError:
                pass


def _git_main_ref(repo):
    from . import gitops as _gitops

    for ref in ("main", "master"):
        if _gitops.git(["rev-parse", "--verify", "--quiet", ref], repo).returncode == 0:
            return ref
    return None


def run_merged_into_main(rec):
    """True if this run's work is ALREADY MERGED into its repo's main — the branch tip is an
    ancestor of main AND nothing's left uncommitted — i.e. it's resolved and safe to clear from
    the inbox/backlog. This is the check `clean`'s base-ahead count CAN'T do: a merged branch still
    counts commits vs its OLD base, so it looks like 'unmerged work' and lingers forever. FAIL-SAFE:
    unverifiable / dirty / no-main → False (never report unconfirmed work as merged)."""
    from . import config as _config
    from . import gitops as _gitops

    (repo, branch) = (rec.get("repo"), rec.get("branch"))
    if not repo or not _config.os.path.isdir(repo):
        return False
    main = _git_main_ref(repo)
    if not main or not branch:
        return False
    if _gitops.git(["rev-parse", "--verify", "--quiet", branch], repo).returncode != 0:
        return True
    (dirty, _) = _gitops.worktree_changes(rec)
    if dirty:
        return False
    return (
        _gitops.git(["merge-base", "--is-ancestor", branch, main], repo).returncode == 0
    )


def _resolve_merged_run(rec):
    """Ack a merged run + advance any linked task to 'merged' + remove its (merged) worktree/branch,
    archiving to permanent history. Safe ONLY for runs run_merged_into_main() confirmed."""
    from . import config as _config
    from . import gitops as _gitops
    from . import runs as _runs
    from . import tasks as _tasks

    for t in _tasks.load_tasks()["tasks"].values():
        if (
            rec["id"] in (t.get("runs") or [])
            and t.get("status") in _tasks.TASK_OPEN_STATUSES
        ):
            _tasks.task_update(t["id"], status="merged")
    (wt, repo, branch) = (rec.get("worktree"), rec.get("repo"), rec.get("branch"))
    if wt and _config.os.path.isdir(wt) and repo and _config.os.path.isdir(repo):
        _gitops.git(["worktree", "remove", "--force", wt], repo)
        if branch:
            _gitops.git(["branch", "-D", branch], repo)
        _gitops.git(["worktree", "prune"], repo)
    elif wt and _config.os.path.isdir(wt):
        _config.shutil.rmtree(wt, ignore_errors=True)
    rec["acknowledged"] = True
    _runs.archive_run(rec)
    remove_logs(rec)
    _runs.log_event(rec, "tidy-resolved")
    try:
        _config.os.remove(_runs.run_path(rec["id"]))
    except OSError:
        pass


def merged_terminal_runs(any_orch=False):
    """Finished runs whose work is already merged into main (resolved, safe to auto-clear)."""
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    out = []
    for rec in _runs.all_runs():
        if _orchestrators.effective_status(
            rec
        ) not in _orchestrators.TERMINAL_STATUSES or _orchestrators.worker_alive(rec):
            continue
        if not any_orch and _orchestrators.owned_by_other_live_orch(rec):
            continue
        if run_merged_into_main(rec):
            out.append(rec)
    return out


def cmd_tidy(argv):
    """Auto-resolve runs whose work is ALREADY MERGED into main — so the completion inbox/backlog
    stops showing done-and-merged work as 'ready to merge'/pending (the clutter that makes the
    orchestrator loop). Acks + cleans them (archived to permanent history first). Only touches runs
    with NO unmerged/uncommitted work; never another live orchestrator's runs without --any.
       neomax tidy [--dry-run] [--any] [--json]"""
    from . import config as _config

    dry = "--dry-run" in argv
    want_json = "--json" in argv
    merged = merged_terminal_runs(any_orch="--any" in argv)
    if want_json:
        print(
            _config.json.dumps(
                [
                    {
                        "id": r["id"],
                        "project": r.get("project"),
                        "branch": r.get("branch"),
                    }
                    for r in merged
                ]
            )
        )
        if not dry:
            for r in merged:
                _resolve_merged_run(r)
        return
    if not merged:
        print(
            "tidy: nothing to resolve — no finished runs are already merged into main."
        )
        return
    print(
        "tidy: %d run(s) already merged into main%s:"
        % (len(merged), " (dry-run)" if dry else "")
    )
    for r in merged:
        print(
            "  %-28s %-12s %s"
            % (r["id"], r.get("project") or "-", (r.get("prompt") or "")[:56])
        )
    if dry:
        print("  → run `neomax tidy` (no --dry-run) to ack + clean them.")
        return
    for r in merged:
        _resolve_merged_run(r)
    print(
        "tidy: resolved %d merged run(s) — acked + cleaned (archived to history). Inbox cleared of them."
        % len(merged)
    )


def cmd_clean(argv):
    from . import config as _config
    from . import gitops as _gitops
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import premerge as _premerge
    from . import run_lifecycle as _run_lifecycle
    from . import runs as _runs

    if not argv:
        _premerge.usage()
    force = "--force" in argv
    any_orch = "--any" in argv
    args = [a for a in argv if a not in ("--force", "--any")]
    bulk = args and args[0] == "--done"
    if bulk:
        targets = [
            r
            for r in _runs.all_runs()
            if _orchestrators.effective_status(r) in _orchestrators.TERMINAL_STATUSES
            and (force or _orchestrators.is_acknowledged(r))
            and (any_orch or not _orchestrators.owned_by_other_live_orch(r))
        ]
        skipped_unacked = [
            r
            for r in _runs.all_runs()
            if _orchestrators.effective_status(r) in _orchestrators.TERMINAL_STATUSES
            and (not _orchestrators.is_acknowledged(r))
            and (not force)
        ]
        skipped_other = [
            r
            for r in _runs.all_runs()
            if _orchestrators.effective_status(r) in _orchestrators.TERMINAL_STATUSES
            and (not any_orch)
            and _orchestrators.owned_by_other_live_orch(r)
        ]
        if skipped_unacked:
            _models.err(
                "neomax: %d finished run(s) NOT cleaned — unacknowledged (orchestrator hasn't received them). `neomax ack --all` then re-run, or clean --force."
                % len(skipped_unacked)
            )
        if skipped_other:
            _models.err(
                "neomax: %d finished run(s) NOT cleaned — owned by ANOTHER live orchestrator (use --any to include them)."
                % len(skipped_other)
            )
    else:
        rec = _runs.load_run(args[0])
        if not rec:
            _models.err("neomax: unknown run %s" % args[0])
            _config.sys.exit(1)
        _run_lifecycle.refuse_if_other_orch(rec, "clean", argv)
        targets = [rec]
    for rec in targets:
        if _orchestrators.worker_alive(rec):
            _models.err(
                "neomax: SKIP %s — worker still running (neomax kill %s first)"
                % (rec["id"], rec["id"])
            )
            continue
        if not force and rec.get("killed") and (rec.get("status") == "aborted"):
            _models.err(
                "neomax: SKIP %s — paused (killed) run kept for resume; `neomax resume %s` or force-discard: neomax clean --force %s"
                % (rec["id"], rec["id"], rec["id"])
            )
            continue
        if not force:
            (dirty, ahead) = _gitops.worktree_changes(rec)
            if dirty or ahead > 0:
                _models.err(
                    "neomax: SKIP %s — worktree has unmerged work (uncommitted=%s, commits_ahead=%d). Merge it, or force-discard: neomax clean --force %s"
                    % (rec["id"], "yes" if dirty else "no", ahead, rec["id"])
                )
                continue
        (wt, repo, branch) = (rec.get("worktree"), rec.get("repo"), rec.get("branch"))
        if wt and _config.os.path.isdir(wt):
            if repo and _config.os.path.isdir(repo):
                _gitops.git(["worktree", "remove", "--force", wt], repo)
                if branch:
                    _gitops.git(["branch", "-D", branch], repo)
            if _config.os.path.isdir(wt):
                _config.shutil.rmtree(wt, ignore_errors=True)
                if repo and _config.os.path.isdir(repo):
                    _gitops.git(["worktree", "prune"], repo)
        _runs.archive_run(rec)
        remove_logs(rec)
        _runs.log_event(rec, "cleaned")
        try:
            _config.os.remove(_runs.run_path(rec["id"]))
        except OSError:
            pass
        print("cleaned %s" % rec["id"])
