"""Usage search, acknowledgement, and audit commands."""


def cmd_find(argv):
    """Agent-affinity lookup: find prior runs that touched a path/keyword, so the
    orchestrator can route related work to the session/account that has context.
       neomax find <keyword>     match files_touched, prompt, branch, or repo
    Prints each match's run id, session, engine/account, status, and how to reuse it."""
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import premerge as _premerge
    from . import runs as _runs

    if not argv:
        _premerge.usage()
    needle = " ".join(argv).lower()
    matches = []
    for rec in _runs.all_runs():
        hay = []
        hay += rec.get("files_touched") or []
        hay.append(rec.get("prompt") or "")
        hay.append(rec.get("branch") or "")
        hay.append(rec.get("repo") or "")
        why = [h for h in hay if needle in str(h).lower()]
        if why:
            matches.append((rec, why))
    if not matches:
        print("no prior run touched %r" % needle)
        return
    matches.sort(key=lambda m: m[0].get("started", 0), reverse=True)
    print("Prior runs with context on %r (most recent first):\n" % needle)
    for rec, why in matches:
        st = _orchestrators.effective_status(rec)
        eng = rec.get("engine", "claude")
        files = rec.get("files_touched") or []
        print(
            "  run %s  [%s/%s]  status=%s  session=%s"
            % (
                rec["id"],
                eng,
                rec.get("profile", "?").split("/")[-1],
                st,
                rec.get("session") or "-",
            )
        )
        print("      prompt: %s" % (rec.get("prompt") or "")[:80].replace("\n", " "))
        if files:
            print(
                "      touched: %s" % ", ".join(files[:6])
                + (" …" if len(files) > 6 else "")
            )
        if (
            eng == "claude"
            and rec.get("session")
            and (
                st in ("done", "error", "aborted", "stalled", "timeout", "interrupted")
            )
        ):
            print(
                '      ↻ reuse context: neomax resume %s "<related task>"' % rec["id"]
            )
        else:
            print(
                '      ↻ route to same account: neomax delegate --engine %s %s "<related task>"'
                % (
                    eng,
                    _models.engine_profiles(eng).index(rec["profile"]) + 1
                    if rec.get("profile") in _models.engine_profiles(eng)
                    else "auto",
                )
            )
        print()


def cmd_ack(argv):
    """Mark finished run(s) acknowledged — the orchestrator has received/processed
    the result. Stops the completion inbox from re-surfacing it. `--all` acks every
    finished run; otherwise ack a specific run id. `--all` does NOT ack another live
    orchestrator's runs (concurrent sessions) without --any."""
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import premerge as _premerge
    from . import run_lifecycle as _run_lifecycle
    from . import runs as _runs

    if not argv:
        _premerge.usage()
    any_orch = "--any" in argv
    if "--all" in argv:
        targets = [
            r
            for r in _runs.all_runs()
            if _orchestrators.effective_status(r)
            in (
                "done",
                "error",
                "limit",
                "stalled",
                "timeout",
                "aborted",
                "interrupted",
            )
            and (any_orch or not _orchestrators.owned_by_other_live_orch(r))
        ]
        skipped_other = [
            r
            for r in _runs.all_runs()
            if _orchestrators.effective_status(r)
            in (
                "done",
                "error",
                "limit",
                "stalled",
                "timeout",
                "aborted",
                "interrupted",
            )
            and (not any_orch)
            and _orchestrators.owned_by_other_live_orch(r)
        ]
        if skipped_other:
            _models.err(
                "neomax: %d finished run(s) NOT acked — owned by ANOTHER live orchestrator (use --any to include them)."
                % len(skipped_other)
            )
    else:
        rid = next((a for a in argv if not a.startswith("-")), None)
        rec = _runs.load_run(rid) if rid else None
        if not rec:
            _models.err("neomax: unknown run %s" % (rid or ""))
            _config.sys.exit(1)
        _run_lifecycle.refuse_if_other_orch(rec, "ack", argv)
        targets = [rec]
    for rec in targets:
        rec["acknowledged"] = True
        _runs.save_run(rec)
        _runs.log_event(rec, "acknowledged")
        print("acknowledged %s" % rec["id"])


def cmd_audit(argv):
    """Print the durable audit timeline — for one run, or the whole ledger."""
    from . import config as _config
    from . import runs as _runs

    runid = argv[0] if argv and (not argv[0].startswith("-")) else None
    events = _runs.read_events(runid)
    if not events:
        print("no audit events recorded" + (" for %s" % runid if runid else ""))
        return
    for e in events:
        ts = _config.time.strftime(
            "%m-%d %H:%M:%S", _config.time.localtime(e.get("ts", 0))
        )
        extra = " ".join(
            (
                "%s=%s" % (k, v)
                for (k, v) in e.items()
                if k
                not in ("ts", "run", "event", "engine", "account", "status", "attempt")
                and v is not None
            )
        )
        print(
            "%s  %-22s %-12s %s/%-14s a%s  %s"
            % (
                ts,
                e.get("run", "-"),
                e.get("event", "-"),
                (e.get("engine") or "?")[:3],
                e.get("account") or "-",
                e.get("attempt") or "-",
                extra,
            )
        )
