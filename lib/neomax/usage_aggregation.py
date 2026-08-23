"""Usage-ledger aggregation and duration parsing."""


def _read_usage_ledger(days):
    """Dedup'd records from the last `days` of ledger (0 = all). Both kinds MAX-win by id
    ('add' on output, 'total' on total tokens) so the final/complete billed value wins and
    re-reads of compacted/streamed files never double- or under-count."""
    from . import config as _config

    if not _config.os.path.isdir(_config.USAGE_LEDGER_DIR):
        return []
    cutoff = _config.time.time() - days * 86400 if days else 0
    (adds, totals) = ({}, {})
    for name in sorted(_config.os.listdir(_config.USAGE_LEDGER_DIR)):
        if not name.endswith(".jsonl"):
            continue
        if days:
            try:
                d = _config.time.mktime(_config.time.strptime(name[:-6], "%Y-%m-%d"))
                if d < cutoff - 86400:
                    continue
            except ValueError:
                pass
        try:
            for line in open(_config.os.path.join(_config.USAGE_LEDGER_DIR, name)):
                try:
                    r = _config.json.loads(line)
                except ValueError:
                    continue
                if cutoff and r.get("ts", 0) < cutoff:
                    continue
                rid = r.get("id")
                if r.get("kind") == "total":
                    cur = totals.get(rid)
                    if (
                        not cur
                        or r["in"] + r["cr"] + r["out"]
                        > cur["in"] + cur["cr"] + cur["out"]
                    ):
                        totals[rid] = r
                else:
                    cur = adds.get(rid)
                    if not cur or r.get("out", 0) > cur.get("out", 0):
                        adds[rid] = r
        except OSError:
            continue
    return list(adds.values()) + list(totals.values())


def gather_usage(days=30):
    """Aggregate exact token records for all five engines."""
    from . import config as _config
    from . import models as _models
    from . import telemetry_grok as _telemetry_grok
    from . import telemetry_kimi as _telemetry_kimi
    from . import telemetry_opencode as _telemetry_opencode

    ledger = _read_usage_ledger(days)
    scan_oc = bool(
        _config.os.environ.get(_models.ENGINES["opencode"]["test_env"])
    ) or _config.os.path.abspath(_config.USAGE_LEDGER_DIR) == _config.os.path.abspath(
        _config.os.path.join(_config.STATE_DIR, "usage-ledger")
    )
    oc_snaps = _telemetry_opencode.opencode_usage_snapshots(days) if scan_oc else []
    scan_kimi = bool(
        _config.os.environ.get(_models.ENGINES["kimi"]["test_env"])
    ) or _config.os.path.abspath(_config.USAGE_LEDGER_DIR) == _config.os.path.abspath(
        _config.os.path.join(_config.STATE_DIR, "usage-ledger")
    )
    kimi_snaps = _telemetry_kimi.kimi_usage_snapshots(days) if scan_kimi else []
    scan_grok = bool(
        _config.os.environ.get(_models.ENGINES["grok"]["test_env"])
    ) or _config.os.path.abspath(_config.USAGE_LEDGER_DIR) == _config.os.path.abspath(
        _config.os.path.join(_config.STATE_DIR, "usage-ledger")
    )
    grok_snaps = _telemetry_grok.grok_usage_snapshots(days) if scan_grok else []
    covered_oc = {s.get("account") for s in oc_snaps if s.get("available")}
    recs = [
        r
        for r in ledger
        if not (r.get("provider") == "opencode" and r.get("account") in covered_oc)
    ]
    covered_kimi = {s.get("account") for s in kimi_snaps if s.get("available")}
    recs = [
        r
        for r in recs
        if not (r.get("provider") == "kimi" and r.get("account") in covered_kimi)
    ]
    covered_grok = {s.get("account") for s in grok_snaps if s.get("available")}
    recs = [
        r
        for r in recs
        if not (r.get("provider") == "grok" and r.get("account") in covered_grok)
    ]
    for snap in oc_snaps:
        recs.extend(snap.get("records") or [])
    for snap in kimi_snaps:
        recs.extend(snap.get("records") or [])
    for snap in grok_snaps:
        recs.extend(snap.get("records") or [])

    def blank():
        return {
            "in": 0,
            "out": 0,
            "reasoning": 0,
            "cw": 0,
            "cr": 0,
            "requests": 0,
            "completions": 0,
            "errors": 0,
            "rate_limits": 0,
            "cost": 0.0,
        }

    (by_model, by_account, by_provider, by_date, by_session, by_agent) = (
        {},
        {},
        {},
        {},
        {},
        {},
    )
    grand = blank()
    for r in recs:
        (prov, acct, model) = (r.get("provider"), r.get("account"), r.get("model"))
        (ni, no) = (
            _telemetry_opencode._opencode_int(r.get("in")),
            _telemetry_opencode._opencode_int(r.get("out")),
        )
        nr = _telemetry_opencode._opencode_int(r.get("reasoning"))
        (ncw, ncr) = (
            _telemetry_opencode._opencode_int(r.get("cw")),
            _telemetry_opencode._opencode_int(r.get("cr")),
        )
        cost = (
            float(r.get("cost"))
            if "cost" in r
            else _config.usage_cost(model, ni, no, ncw, ncr)
        )
        completions = _telemetry_opencode._opencode_int(r.get("completions", 1))
        requests = _telemetry_opencode._opencode_int(
            r.get("requests", completions or 1)
        )
        (errors, rate_limits) = (
            _telemetry_opencode._opencode_int(r.get("errors")),
            _telemetry_opencode._opencode_int(r.get("rate_limits")),
        )
        day = _config.time.strftime(
            "%Y-%m-%d", _config.time.localtime(r.get("ts") or 0)
        )
        sess = r.get("session") or r.get("id")
        targets = [
            (by_model, (prov, model)),
            (by_account, (prov, acct)),
            (by_provider, prov),
            (by_date, day),
            (by_session, (prov, sess)),
        ]
        if r.get("agent"):
            targets.append((by_agent, (prov, acct, r.get("agent"))))
        targets.append((None, None))
        for d, key in targets:
            tgt = grand if d is None else d.setdefault(key, blank())
            tgt["in"] += ni
            tgt["out"] += no
            tgt["reasoning"] += nr
            tgt["cw"] += ncw
            tgt["cr"] += ncr
            tgt["requests"] += requests
            tgt["completions"] += completions
            tgt["errors"] += errors
            tgt["rate_limits"] += rate_limits
            tgt["cost"] += cost

    def rows(d, keyname):
        out = []
        for k, v in d.items():
            row = dict(v)
            row["cost"] = round(v["cost"], 2)
            if keyname == "model":
                (row["provider"], row["model"]) = k
            elif keyname == "account":
                (row["provider"], row["account"]) = k
            else:
                row["provider"] = k
            out.append(row)
        return sorted(
            out, key=lambda r: (-r["cost"], -(r["out"] + r["reasoning"] + r["in"]))
        )

    def daterows():
        out = []
        for day, v in by_date.items():
            row = dict(v)
            row["date"] = day
            row["cost"] = round(v["cost"], 2)
            out.append(row)
        return sorted(out, key=lambda r: r["date"], reverse=True)

    def sessrows(limit=50):
        out = []
        for (prov, sess), v in by_session.items():
            row = dict(v)
            row["provider"] = prov
            row["session"] = sess
            row["cost"] = round(v["cost"], 2)
            out.append(row)
        return sorted(
            out, key=lambda r: (-r["cost"], -(r["out"] + r["reasoning"] + r["in"]))
        )[:limit]

    def agentrows():
        out = []
        for (prov, acct, agent), v in by_agent.items():
            row = dict(v)
            row.update({"provider": prov, "account": acct, "agent": agent})
            row["cost"] = round(v["cost"], 2)
            out.append(row)
        return sorted(out, key=lambda r: (-r["cost"], -(r["out"] + r["reasoning"])))

    oc_portal = []
    for s in oc_snaps:
        oc_portal.append(
            {
                k: s.get(k)
                for k in (
                    "available",
                    "source",
                    "database",
                    "db_bytes",
                    "account",
                    "window_days",
                    "totals",
                    "models",
                    "agents",
                    "tool_usage",
                    "last_error",
                    "error",
                )
            }
        )
    kimi_portal = [
        {
            k: s.get(k)
            for k in (
                "available",
                "source",
                "account",
                "window_days",
                "totals",
                "models",
            )
        }
        for s in kimi_snaps
    ]
    grok_portal = [
        {
            k: s.get(k)
            for k in (
                "available",
                "source",
                "account",
                "window_days",
                "totals",
                "models",
            )
        }
        for s in grok_snaps
    ]
    grand["cost"] = round(grand["cost"], 2)
    return {
        "days": days,
        "now": int(_config.time.time()),
        "grand": grand,
        "by_provider": rows(by_provider, "provider"),
        "by_account": rows(by_account, "account"),
        "by_model": rows(by_model, "model"),
        "by_date": daterows(),
        "by_session": sessrows(),
        "by_agent": agentrows(),
        "opencode": oc_portal,
        "kimi": kimi_portal,
        "grok": grok_portal,
        "pricing": _config.PRICING,
    }


def _parse_duration_s(s):
    """A duration string → seconds. '90s'/'37m'/'2h'/'1d' or a bare number (=seconds).
    None if unparseable."""
    from . import config as _config

    s = str(s).strip().lower()
    m = _config.re.fullmatch("(\\d+(?:\\.\\d+)?)\\s*([smhd]?)", s)
    if not m:
        return None
    n = float(m.group(1))
    return n * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
