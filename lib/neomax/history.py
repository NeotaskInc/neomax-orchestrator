"""Permanent run-history command."""


def cmd_history(argv):
    """Permanent run history — every run ever, even after `clean`.
    neomax history [--json] [N]      list the last N runs (default 40)
    neomax history <run-id>          show one run's full detail
    neomax history <run-id> --log    print that run's archived worker log
    """
    from . import config as _config
    from . import models as _models
    from . import runs as _runs

    if argv and argv[0] not in ("--json",) and (not argv[0].lstrip("-").isdigit()):
        rid = argv[0]
        rec = _runs.history_get(rid)
        if not rec:
            _models.err("neomax history: no archived run %s" % rid)
            _config.sys.exit(1)
        if "--log" in argv:
            lp = rec.get("_log_path")
            if lp and _config.os.path.isfile(lp):
                with open(lp) as f:
                    _config.sys.stdout.write(f.read())
            else:
                _models.err("neomax history: no archived log for %s" % rid)
            return
        if "--json" in argv:
            print(_config.json.dumps(rec))
            return
        print(
            "run %s [%s/%s] %s"
            % (
                rec["id"],
                rec.get("engine"),
                _config.os.path.basename(rec.get("profile", "")),
                rec.get("status"),
            )
        )
        print("  prompt: %s" % (rec.get("prompt") or "")[:300])
        for k in ("branch", "repo", "tag", "goal", "effort", "pr_url"):
            if rec.get(k):
                print("  %s: %s" % (k, rec[k]))
        ch = rec.get("children") or []
        if ch:
            print("  sub-agents (%d):" % len(ch))
            for c in ch[:30]:
                print("    - [%s] %s" % (c.get("status"), c.get("label")))
        if rec.get("_log_path"):
            print("  log: neomax history %s --log" % rid)
        return
    limit = 40
    for a in argv:
        if a.lstrip("-").isdigit():
            limit = int(a.lstrip("-"))
    rows = _runs.history_runs(limit=limit)
    if "--json" in argv:
        print(_config.json.dumps(rows))
        return
    if not rows:
        print("no run history yet")
        return
    print(
        "%-22s %-7s %-7s %-9s %4s  %s" % ("RUN", "ENG", "ACCT", "STATUS", "SUB", "TASK")
    )
    for r in rows:
        when = _config.time.strftime(
            "%m-%d %H:%M",
            _config.time.localtime(r.get("ended") or r.get("started") or 0),
        )
        sub = "+%d" % r["children"] if r.get("children") else ""
        print(
            "%-22s %-7s %-7s %-9s %4s  %s  %s"
            % (
                r["id"],
                r.get("engine", "")[:6],
                (r.get("account") or "")[-6:],
                r.get("status", ""),
                sub,
                when,
                (r.get("prompt") or "")[:60],
            )
        )
    print(
        "\n%d run(s) in history · detail: neomax history <id> · log: neomax history <id> --log"
        % len(rows)
    )
