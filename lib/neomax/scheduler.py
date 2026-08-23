"""Dependency-aware multi-run scheduler."""


def cmd_run_all(argv):
    """Fan-out SCHEDULER: run a decomposed PLAN across many accounts/engines for
    minimum wall-clock — respecting depends_on (blocking) and area overlap, bounded
    by a capacity cap. Each part is a worker cut from a shared integration branch;
    finished parts are merged into it. Leaves the integration branch ready to PR.

       neomax run-all PLAN.json
    Plan JSON: {repo?, base?, integration_branch?, parts:[
        {id, prompt, engine?(claude|codex|opencode|kimi|grok), model?, area?[paths], depends_on?[ids], effort?, ultra?} ]}
    """
    from . import area_locks as _area_locks
    from . import config as _config
    from . import gitops as _gitops
    from . import merge as _merge
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import premerge as _premerge
    from . import reconcile as _reconcile
    from . import runs as _runs
    from . import selection as _selection
    from . import usage_windows as _usage_windows

    if not argv:
        _premerge.usage()
    try:
        plan = _config.json.load(open(argv[0]))
    except (OSError, ValueError) as e:
        _models.err("neomax: cannot read plan %s (%s)" % (argv[0], e))
        _config.sys.exit(2)
    parts_in = plan.get("parts") or []
    if not parts_in:
        _models.err("neomax run-all: plan has no parts")
        _config.sys.exit(2)
    repo = (
        plan.get("repo")
        or _gitops.git(
            ["rev-parse", "--show-toplevel"], _config.os.getcwd()
        ).stdout.strip()
    )
    if not repo or not _config.os.path.isdir(repo):
        _models.err(
            "neomax run-all: plan repo not found (set 'repo' or run inside the repo)"
        )
        _config.sys.exit(2)
    base = plan.get("base") or _gitops.default_branch(repo)
    planid = plan.get("plan") or _config.time.strftime("%Y%m%d-%H%M%S") + "-" + str(
        _config.os.getpid()
    )
    integ = plan.get("integration_branch") or "neomax/int-" + planid
    poll = float(_config.os.environ.get("NEOMAX_POLL", "5"))
    if _gitops.git(["rev-parse", "--verify", integ], repo).returncode != 0:
        if _gitops.git(["branch", integ, base], repo).returncode != 0:
            _models.err("neomax run-all: cannot create integration branch %s" % integ)
            _config.sys.exit(2)
    _config.os.makedirs(_config.WORKTREES_DIR, exist_ok=True)
    integ_wt = _config.os.path.join(_config.WORKTREES_DIR, "integ-%s" % planid)
    if not _config.os.path.isdir(integ_wt):
        _gitops.git(["worktree", "prune"], repo)
        if _gitops.git(["worktree", "add", integ_wt, integ], repo).returncode != 0:
            _models.err("neomax run-all: cannot create integration worktree")
            _config.sys.exit(2)

    def _norm_keys(raw):
        """Normalize a part's area / depends_on field to a clean set of non-empty strings.
        A bare string → that single key (tolerant). Any non-list/str garbage → empty set
        (for area that means the fail-safe GLOBAL lock; for deps, no deps). Coercing to str
        keeps downstream _lock_path(area.replace(...)) and sorted() crash-proof on a
        hand-authored plan with a stray number/object in the list."""
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple, set)):
            return set()
        return {str(x).strip() for x in raw if str(x).strip()}

    scope = _config.allowed_engines()
    default_part_engine = next(
        (e for e in ("claude", "opencode", "grok", "kimi", "codex") if e in scope),
        "claude",
    )
    P = {}
    for i, p in enumerate(parts_in):
        if not isinstance(p, dict):
            _models.err("neomax run-all: part #%d is not an object" % (i + 1))
            _config.sys.exit(2)
        pid = p.get("id") or "p%d" % (i + 1)
        if not _config.re.match("^[A-Za-z0-9._-]+$", str(pid)):
            _models.err("neomax run-all: invalid part id %r (use [A-Za-z0-9._-])" % pid)
            _config.sys.exit(2)
        if not p.get("prompt"):
            _models.err("neomax run-all: part %s has no prompt" % pid)
            _config.sys.exit(2)
        eng = p.get("engine", default_part_engine)
        if eng not in _models.ENGINES:
            _models.err("neomax run-all: part %s has unknown engine %r" % (pid, eng))
            _config.sys.exit(2)
        if not _config.engine_in_scope(eng):
            _models.err(
                "neomax run-all: part %s uses engine %r, out of fleet scope (NEOMAX_FLEET=%s)"
                % (pid, eng, _config.os.environ.get("NEOMAX_FLEET", "all"))
            )
            _config.sys.exit(2)
        eff = p.get("effort")
        allowed = (
            ()
            if eng in ("opencode", "kimi", "grok")
            else _models.CODEX_EFFORTS
            if eng == "codex"
            else ("low", "medium", "high", "xhigh", "max")
        )
        if eff and eff not in allowed:
            _models.err(
                "neomax run-all: part %s effort %r invalid for %s (%s)"
                % (pid, eff, eng, "|".join(allowed))
            )
            _config.sys.exit(2)
        if p.get("opus") and eng != "claude":
            _models.err(
                "neomax run-all: part %s sets opus, which is a Claude-only option — remove it from the %s part"
                % (pid, eng)
            )
            _config.sys.exit(2)
        if eng in ("opencode", "kimi", "grok") and p.get("ultra"):
            _models.err(
                "neomax run-all: part %s sets ultra; %s does not support it"
                % (pid, eng)
            )
            _config.sys.exit(2)
        explicit_model = p.get("model")
        cxm = p.get("codex_model")
        if cxm and eng != "codex":
            _models.err(
                "neomax run-all: part %s sets codex_model on a %s part" % (pid, eng)
            )
            _config.sys.exit(2)
        if cxm:
            _models.resolve_codex_model(cxm)
        kim = p.get("kimi_model")
        if kim and eng != "kimi":
            _models.err(
                "neomax run-all: part %s sets kimi_model on a %s part" % (pid, eng)
            )
            _config.sys.exit(2)
        if kim:
            _models.resolve_kimi_model(kim)
        if explicit_model and (cxm or kim):
            _models.err(
                "neomax run-all: part %s cannot combine model with a legacy engine model key"
                % pid
            )
            _config.sys.exit(2)
        if explicit_model:
            {
                "claude": _models.resolve_claude_model,
                "codex": _models.resolve_codex_model,
                "opencode": _models.resolve_opencode_model,
                "kimi": _models.resolve_kimi_model,
                "grok": _models.resolve_grok_model,
            }[eng](explicit_model)
        if (
            p.get("opus")
            and explicit_model
            and (
                _models.resolve_claude_model(explicit_model).split("[")[0]
                != _config.CLAUDE_OPUS_MODEL
            )
        ):
            _models.err(
                "neomax run-all: part %s combines opus with a different Claude model"
                % pid
            )
            _config.sys.exit(2)
        if pid in P:
            _models.err("neomax run-all: duplicate part id %r" % pid)
            _config.sys.exit(2)
        P[pid] = {
            "id": pid,
            "prompt": p["prompt"],
            "engine": eng,
            "area": _norm_keys(p.get("area")),
            "deps": _norm_keys(p.get("depends_on")),
            "effort": eff,
            "ultra": bool(p.get("ultra")),
            "opus": bool(p.get("opus")),
            "model": explicit_model,
            "codex_model": cxm,
            "kimi_model": kim,
            "state": "pending",
            "runid": None,
            "branch": None,
            "launched_at": 0,
        }
    for p in P.values():
        unknown = p["deps"] - set(P)
        if unknown:
            _models.err(
                "neomax run-all: part %s depends on unknown part(s): %s"
                % (p["id"], ", ".join(sorted(unknown)))
            )
            _config.sys.exit(2)
    indeg = {pid: len(P[pid]["deps"]) for pid in P}
    dependents = {pid: [q for q in P if pid in P[q]["deps"]] for pid in P}
    queue = [pid for (pid, d) in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in dependents[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen != len(P):
        _models.err("neomax run-all: dependency CYCLE detected — cannot schedule")
        _config.sys.exit(2)
    CAP = (
        int(_config.os.environ.get("NEOMAX_MAX_LIVE", "0"))
        or _usage_windows.default_fanout_cap()
    )
    CAP = max(1, min(CAP, _usage_windows.FLEET_CONCURRENCY_CAP))
    _models.err(
        "neomax run-all: plan %s — %d parts, integration %s (base %s), cap %d concurrent"
        % (planid, len(P), integ, base, CAP)
    )
    _runs.log_event(
        {"id": planid, "engine": "-"},
        "plan_started",
        parts=len(P),
        cap=CAP,
        integ=integ,
    )
    self_path = _config.os.path.abspath(_config.sys.argv[0])

    def part_areas(p):
        return sorted(p["area"]) or ["*"]

    def ready():
        out = [
            p
            for p in P.values()
            if p["state"] == "pending"
            and all((P.get(d, {}).get("state") == "done" for d in p["deps"]))
        ]
        out.sort(key=lambda p: -sum((1 for q in P.values() if p["id"] in q["deps"])))
        return out

    def live():
        return sum((1 for p in P.values() if p["state"] == "running"))

    def resolve_part(rid):
        """A part merged into the integration branch: ack it (out of the inbox) and
        drop its now-redundant worktree+branch (commits live on integ). Audit kept."""
        rec = _runs.load_run(rid)
        if not rec:
            return
        rec["acknowledged"] = True
        rec["status"] = "integrated"
        _runs.save_run(rec)
        _runs.archive_run(rec)
        _runs.log_event(rec, "integrated", plan=planid)
        (wt, r, br) = (rec.get("worktree"), rec.get("repo"), rec.get("branch"))
        if wt and _config.os.path.isdir(wt) and r:
            _gitops.git(["worktree", "remove", "--force", wt], r)
            if br:
                _gitops.git(["branch", "-D", br], r)

    interrupted = {"v": False}

    def on_sig(signum, _f):
        interrupted["v"] = True
        raise KeyboardInterrupt

    for s in (_config.signal.SIGINT, _config.signal.SIGTERM, _config.signal.SIGHUP):
        _config.signal.signal(s, on_sig)
    max_stall = int(_config.os.environ.get("NEOMAX_MAX_STALL", "360"))
    stalls = 0
    try:
        while any((p["state"] in ("pending", "running") for p in P.values())):
            for p in P.values():
                if p["state"] == "pending" and any(
                    (
                        P.get(d, {}).get("state") in ("failed", "conflict", "blocked")
                        for d in p["deps"]
                    )
                ):
                    p["state"] = "blocked"
                    _models.err(
                        "  ⛔ %s BLOCKED — a dependency did not succeed" % p["id"]
                    )
            progressed = False
            while (
                live() < CAP
                and _usage_windows.fleet_live_workers()
                < _usage_windows.FLEET_CONCURRENCY_CAP
            ):
                launched = False
                for p in ready():
                    rid = "%s-%s" % (planid, p["id"])
                    got = []
                    for a in part_areas(p):
                        if _area_locks.acquire_area_lock(repo, a, rid):
                            got.append(a)
                        else:
                            break
                    if len(got) != len(part_areas(p)):
                        _area_locks.release_area_locks(repo, got, rid)
                        continue
                    bias = {}
                    for q in P.values():
                        if (
                            q["state"] == "running"
                            and q["engine"] == p["engine"]
                            and q.get("profile")
                        ):
                            bias[q["profile"]] = bias.get(q["profile"], 0) + 100.0
                    try:
                        profile = _selection.pick_account(
                            "auto", engine=p["engine"], bias=bias
                        )
                    except SystemExit:
                        _area_locks.release_area_locks(repo, got, rid)
                        continue
                    acctno = _models.engine_profiles(p["engine"]).index(profile) + 1
                    args = [
                        self_path,
                        "--engine",
                        p["engine"],
                        "--base",
                        integ,
                        "--run-id",
                        rid,
                        "--tag",
                        planid,
                    ]
                    if p["effort"]:
                        args += ["-e", p["effort"]]
                    if p["ultra"]:
                        args += ["-u"]
                    if p["opus"]:
                        args += ["--opus"]
                    if p.get("model"):
                        args += ["--model", p["model"]]
                    if p.get("codex_model"):
                        args += ["--codex-model", p["codex_model"]]
                    if p.get("kimi_model"):
                        args += ["--kimi-model", p["kimi_model"]]
                    args += [str(acctno), p["prompt"]]
                    _config.os.makedirs(_config.LOGS_DIR, exist_ok=True)
                    logf = open(
                        _config.os.path.join(_config.LOGS_DIR, "runall-%s.log" % rid),
                        "ab",
                    )
                    _config.subprocess.Popen(
                        args,
                        cwd=repo,
                        stdout=logf,
                        stderr=logf,
                        stdin=_config.subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    (p["state"], p["runid"], p["branch"]) = (
                        "running",
                        rid,
                        "neomax/" + rid,
                    )
                    p["profile"] = profile
                    p["launched_at"] = _config.time.time()
                    _models.err(
                        "  ▶ launched %s on %s acct %d (run %s) [areas: %s]"
                        % (p["id"], p["engine"], acctno, rid, ", ".join(part_areas(p)))
                    )
                    launched = progressed = True
                    break
                if not launched:
                    break
            for p in P.values():
                if p["state"] != "running":
                    continue
                rec = _runs.load_run(p["runid"])
                if not rec:
                    if _config.time.time() - p.get("launched_at", 0) > 30:
                        p["state"] = "failed"
                        _area_locks.release_area_locks(repo, part_areas(p), p["runid"])
                        _models.err(
                            "  ✗ %s FAILED to start (no run record after 30s) — check %s"
                            % (
                                p["id"],
                                _config.os.path.join(
                                    _config.LOGS_DIR, "runall-%s.log" % p["runid"]
                                ),
                            )
                        )
                        progressed = True
                    continue
                st = _orchestrators.effective_status(rec)
                if st == "done":
                    ok = _merge.integrate_part(
                        repo, integ_wt, integ, p["branch"], p["id"]
                    )
                    p["state"] = "done" if ok else "conflict"
                    _area_locks.release_area_locks(repo, part_areas(p), p["runid"])
                    if ok:
                        resolve_part(p["runid"])
                    _models.err(
                        "  ✓ %s %s"
                        % (
                            p["id"],
                            "done + integrated"
                            if ok
                            else "done but CONFLICT (resolve on %s)" % integ,
                        )
                    )
                    progressed = True
                elif st == "orphaned":
                    _reconcile.kill_worker_inline(rec)
                    p["state"] = "failed"
                    _area_locks.release_area_locks(repo, part_areas(p), p["runid"])
                    _models.err(
                        "  ✗ %s ORPHANED (supervisor died) — killed worker, marked failed; heal: neomax retry %s"
                        % (p["id"], p["runid"])
                    )
                    progressed = True
                elif st != "running":
                    p["state"] = "failed"
                    _area_locks.release_area_locks(repo, part_areas(p), p["runid"])
                    _models.err(
                        "  ✗ %s FAILED (%s) — auto-heal: neomax reconcile --heal, or: neomax retry %s"
                        % (p["id"], st, p["runid"])
                    )
                    progressed = True
            if (
                not progressed
                and live() == 0
                and any((p["state"] == "pending" for p in P.values()))
            ):
                stalls += 1
                if stalls >= max_stall:
                    for p in P.values():
                        if p["state"] == "pending":
                            p["state"] = "blocked"
                    _models.err(
                        "  ⛔ no progress for %d cycles — remaining parts blocked on capacity/locks; stopping"
                        % stalls
                    )
            else:
                stalls = 0
            if any((p["state"] in ("pending", "running") for p in P.values())):
                _config.time.sleep(poll)
    except KeyboardInterrupt:
        _models.err(
            "\nneomax run-all: INTERRUPTED — detached part workers keep running (tracked in the registry); resume with `neomax reconcile --heal` or re-run the plan. Released area-locks for non-running parts."
        )
    finally:
        for p in P.values():
            if p["state"] != "running":
                _area_locks.release_area_locks(
                    repo, part_areas(p), "%s-%s" % (planid, p["id"])
                )
    done = [p for p in P.values() if p["state"] == "done"]
    bad = [p for p in P.values() if p["state"] in ("failed", "conflict", "blocked")]
    _runs.log_event(
        {"id": planid, "engine": "-"}, "plan_finished", done=len(done), failed=len(bad)
    )
    _models.err(
        "\nneomax run-all: %d/%d parts integrated onto %s%s"
        % (
            len(done),
            len(P),
            integ,
            ""
            if not bad
            else "; %d need attention: %s"
            % (len(bad), ", ".join(("%s(%s)" % (p["id"], p["state"]) for p in bad))),
        )
    )
    _models.err(
        "  → CLOSE THE BATCH: review `git -C %s diff %s..%s`, then run `/no-mistakes` on branch %s to push + open a GREEN PR (or `neomax pr --branch %s --base %s` for a plain draft PR). Merge to main stays gated on your OK: `neomax shepherd --branch %s --merge`."
        % (repo, base, integ, integ, integ, base, integ)
    )
    if not bad:
        _gitops.git(["worktree", "remove", "--force", integ_wt], repo)
    print(
        "PLAN %s: %d/%d integrated, %d outstanding"
        % (planid, len(done), len(P), len(bad))
    )
