"""Usage and fleet-status commands."""


def cmd_usage(argv):
    """Token usage + cost across every recorded request (Claude + Codex + OpenCode + Kimi + Grok), per account /
    model / provider — the API-equivalent value of your subscriptions. Covers EVERY agent that
    wrote a transcript: the orchestrator, delegated workers, AND their sub-agents.
       neomax usage [--json] [--days N]            (default 30 days; 0 = all-time)
       neomax usage [--json] --since 37m|2h|90s    a precise recent window — e.g. sum a just-
                                                      finished goal/session's WHOLE-FLEET cost
       neomax usage [--json] --minutes N | --hours N"""
    from . import config as _config
    from . import usage_aggregation as _usage_aggregation

    days = 30
    window_label = None
    for i, a in enumerate(argv):
        if a == "--days" and i + 1 < len(argv):
            try:
                days = int(argv[i + 1])
            except ValueError:
                pass
        elif a == "--all":
            days = 0
        elif a in (
            "--since",
            "--minutes",
            "--mins",
            "--hours",
            "--hrs",
        ) and i + 1 < len(argv):
            raw = argv[i + 1]
            secs = (
                _usage_aggregation._parse_duration_s(raw)
                if a == "--since"
                else _usage_aggregation._parse_duration_s(raw + "m")
                if a in ("--minutes", "--mins")
                else _usage_aggregation._parse_duration_s(raw + "h")
            )
            if secs and secs > 0:
                days = secs / 86400.0
                mins = secs / 60.0
                window_label = (
                    "last %dm" % round(mins)
                    if mins < 120
                    else "last %.1fh" % (secs / 3600.0)
                )
    u = _usage_aggregation.gather_usage(days)
    if "--json" in argv:
        if window_label:
            u["window"] = window_label
        print(_config.json.dumps(u))
        return

    def fmt(n):
        if n >= 1000000000.0:
            return "%.2fB" % (n / 1000000000.0)
        return (
            "%.1fM" % (n / 1000000.0)
            if n >= 1000000.0
            else "%.0fk" % (n / 1000.0)
            if n >= 1000
            else str(n)
        )

    g = u["grand"]
    span = window_label or ("all time" if not days else "%d days" % round(days))
    print(
        "TOKEN USAGE — %s · WHOLE FLEET (orchestrator + workers + sub-agents) · est. API-equivalent value of your subscriptions"
        % span
    )
    print(
        "  total: in %s · out %s · reasoning %s · cache-w %s · cache-r %s · %d/%d completions/requests · %d errors (%d rate limits) · ~$%.2f"
        % (
            fmt(g["in"]),
            fmt(g["out"]),
            fmt(g.get("reasoning", 0)),
            fmt(g["cw"]),
            fmt(g["cr"]),
            g["completions"],
            g.get("requests", g["completions"]),
            g.get("errors", 0),
            g.get("rate_limits", 0),
            g["cost"],
        )
    )
    for title, key in (
        ("by provider", "by_provider"),
        ("by model", "by_model"),
        ("by account", "by_account"),
    ):
        if not u[key]:
            continue
        print("\n%s:" % title)
        for r in u[key]:
            label = r.get("model") or r.get("account") or r.get("provider")
            print(
                "  %-26s in %-7s out %-7s reason %-7s cache(w %-6s r %-7s) %5d/%-5d done/req %3d err %3d 429  ~$%.2f"
                % (
                    "%s/%s" % (r["provider"], label)
                    if r.get("model") or r.get("account")
                    else label,
                    fmt(r["in"]),
                    fmt(r["out"]),
                    fmt(r.get("reasoning", 0)),
                    fmt(r["cw"]),
                    fmt(r["cr"]),
                    r["completions"],
                    r.get("requests", r["completions"]),
                    r.get("errors", 0),
                    r.get("rate_limits", 0),
                    r["cost"],
                )
            )
    if u.get("by_date"):
        print("\nby date (newest first):")
        for r in u["by_date"]:
            print(
                "  %s   in %-7s out %-7s cache(w %-6s r %-7s) %5d compl  ~$%.2f"
                % (
                    r["date"],
                    fmt(r["in"]),
                    fmt(r["out"]),
                    fmt(r["cw"]),
                    fmt(r["cr"]),
                    r["completions"],
                    r["cost"],
                )
            )
    print(
        "\n($ = estimate at API rates; counts exact. Cache rates are the providers' documented"
    )
    print(
        " multipliers of input (read 0.10×, Claude write 1.25×); edit MODEL_IO in neomax.)"
    )


def cmd_status(argv):
    from . import config as _config
    from . import status as _status

    if "--json" in argv:
        print(_config.json.dumps(_status.gather_status()))
        return
    s = _status.gather_status()
    orchs = [o for o in s.get("orchestrators") or [] if o.get("live")]
    orch_dirs = {(o.get("engine"), o.get("account_dir")) for o in orchs}
    if orchs:
        print("ACTIVE ORCHESTRATORS:")
        for o in orchs:
            email = next(
                (
                    a.get("email")
                    for a in s["engines"].get(o.get("engine"), {}).get("accounts", [])
                    if a.get("name") == o.get("account_dir")
                ),
                None,
            )
            print(
                "  ★ %s acct %s%s · project %s · session %s"
                % (
                    o.get("engine"),
                    o.get("account"),
                    " (%s)" % email if email else "",
                    o.get("project") or "—",
                    str(o.get("session") or "")[:24],
                )
            )
    for engine in ("claude", "codex", "opencode", "kimi", "grok"):
        print("\n%s accounts:" % engine.upper())
        for a in s["engines"][engine]["accounts"]:
            auth = "logged in" if a["authenticated"] else "NOT logged in"
            win = ""
            u = a.get("usage") or {}
            fh = (u.get("five_hour") or {}).get("used_percent")
            wk = (u.get("seven_day") or {}).get("used_percent")
            if fh is not None:
                win = " · 5h %.0f%%" % fh
            if wk is not None:
                win += " · 7d %.0f%%" % wk
            if engine == "opencode":
                ot = (a.get("telemetry") or {}).get("totals") or {}
                if ot.get("requests") or ot.get("out"):
                    win += (
                        " · local 7d: %s out / %s in · %s requests · %s errors · %s 429s"
                        % (
                            ot.get("out", 0),
                            ot.get("in", 0),
                            ot.get("requests", 0),
                            ot.get("errors", 0),
                            ot.get("rate_limits", 0),
                        )
                    )
            flags = ""
            if a.get("cooldown_until", 0) > s["now"]:
                flags += " · cooldown %dm" % ((a["cooldown_until"] - s["now"]) / 60)
            if a.get("paused"):
                flags += " · ⏸ PAUSED"
            if (
                engine == "kimi"
                and a.get("authenticated")
                and (not a.get("worker_eligible"))
            ):
                flags += " · native CLI only (OAuth required for K3 pool)"
            if (engine, a.get("name")) in orch_dirs:
                flags += " · ★ ORCHESTRATOR"
            print(
                "  account %s  %-22s %d live · %s%s%s"
                % (a["n"], a["name"], a["live"], auth, win, flags)
            )
    print("\nruns: %d total, %d in completion inbox" % (len(s["runs"]), s["inbox"]))
