"""Usage-aware Neomax orchestrator selection."""

ORCH_ANTI_STACK_WEIGHT = 100000.0


def pick_orch_account(engine="claude"):
    """The best account to RUN THE INTERACTIVE ORCHESTRATOR on (every provider launcher picks
    here — the orchestrator session burns quota too, so it must NOT start on a weekly-exhausted
    or PAUSED account). HARD constraints: logged-in, NOT paused, NOT in cooldown, weekly <99%
    (hard wall). Among those, prefer the FRESHEST (lowest 5h + weekly) and least-live. Returns
    a profile path, or None if no eligible account (caller falls back). This is what worker
    dispatch already did via pick_account — the orchestrator launcher used to ignore it."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import quota_deadlines as _quota_deadlines
    from . import usage_windows as _usage_windows

    cands = []
    for p in _models.engine_profiles(engine):
        if (
            not _account_auth.logged_in(p, engine)
            or _account_state.is_paused(p)
            or _account_state.cooling_down(p)
        ):
            continue
        if _usage_windows.weekly_window(p, engine) >= _usage_windows.SEVEN_D_HARD:
            continue
        cands.append(p)
    if not cands:
        return None
    counts = _account_state.live_counts(engine)
    me = _orchestrators.current_orch_session()
    orch_here = {}
    for o in _orchestrators.live_orchestrators():
        if o.get("engine", "claude") != engine or (me and o.get("session") == me):
            continue
        ad = o.get("account_dir")
        if ad:
            orch_here[ad] = orch_here.get(ad, 0) + 1
    return min(
        cands,
        key=lambda p: (
            1
            if _usage_windows.usage_window(p, engine)
            >= _quota_deadlines.ROTATE_5H_CEILING
            else 0,
            orch_here.get(_config.os.path.basename(p), 0) * ORCH_ANTI_STACK_WEIGHT
            + _usage_windows.usage_window(p, engine)
            + _usage_windows.weekly_window(p, engine)
            + counts.get(p, 0) * 100.0
            + _quota_deadlines.weekly_deadline_tier(p, engine)
            * _quota_deadlines.WEEKLY_TIEBREAK_WEIGHT,
        ),
    )


def cmd_pick_orch(argv):
    """Print the profile PATH of the best orchestrator account (for any provider launcher).
    Prints nothing if none eligible — the launcher then falls back.  neomax pick-orch [--engine ...]"""
    from . import models as _models

    engine = "claude"
    if "--engine" in argv:
        i = argv.index("--engine")
        if i + 1 < len(argv):
            engine = argv[i + 1]
    if engine not in _models.ENGINES:
        return
    p = pick_orch_account(engine)
    if p:
        print(p)


def _neomax_priority(raw=None):
    from . import config as _config
    from . import models as _models

    raw = (
        raw
        or _config.os.environ.get("NEOMAX_ENGINE_PRIORITY")
        or "claude,codex,kimi,grok,opencode"
    )
    requested = [x.strip().lower() for x in _config.re.split("[,+]", raw) if x.strip()]
    invalid = [x for x in requested if x not in _models.ENGINES]
    if invalid:
        _models.err(
            "neomax pick-neomax: invalid engine priority: %s" % ", ".join(invalid)
        )
        _config.sys.exit(2)
    ordered = []
    for engine in requested + list(_models.ENGINES):
        if engine not in ordered:
            ordered.append(engine)
    return ordered


def _neomax_selection_state():
    from . import config as _config

    path = _config.os.path.join(_config.STATE_DIR, "neomax-selection.json")
    try:
        with open(path) as f:
            data = _config.json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _neomax_usage_pressure(profile, engine):
    from . import usage_windows as _usage_windows

    data = _usage_windows.fetch_engine_usage(profile, engine)
    if not isinstance(data, dict) or data.get("expired"):
        return None
    values = []
    if _usage_windows.engine_has_5h(engine):
        value = (data.get("five_hour") or {}).get("used_percent")
        if value is not None:
            values.append(float(value))
    value = (data.get("seven_day") or {}).get("used_percent")
    if value is not None:
        values.append(float(value))
    return max(values) if values else None


def pick_neomax_orchestrator(
    priority=None, forced_engine=None, cwd=None, resume=False, dedicated=False
):
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import models as _models
    from . import usage_windows as _usage_windows

    order = _neomax_priority(priority)
    worker_engines = [engine for engine in order if pick_orch_account(engine)]
    candidates = {}
    for engine in order:
        profile = (
            _models.orch_profile(engine) if dedicated else pick_orch_account(engine)
        )
        if (
            dedicated
            and profile
            and (
                not _account_auth.logged_in(profile, engine)
                or _account_state.is_paused(profile)
                or _account_state.cooling_down(profile)
                or (
                    _usage_windows.weekly_window(profile, engine)
                    >= _usage_windows.SEVEN_D_HARD
                )
            )
        ):
            profile = None
        if not profile:
            continue
        pressure = _neomax_usage_pressure(profile, engine)
        live = _account_state.live_counts(engine).get(profile, 0)
        candidates[engine] = {
            "engine": engine,
            "profile": profile,
            "pressure": pressure,
            "live": live,
        }
    if forced_engine:
        if forced_engine not in _models.ENGINES:
            _models.err(
                "neomax pick-neomax: --engine must be one of %s"
                % "|".join(_models.ENGINES)
            )
            _config.sys.exit(2)
        if forced_engine not in candidates:
            return None
        chosen = candidates[forced_engine]
        reason = "explicit --engine %s" % forced_engine
    else:
        previous = (_neomax_selection_state().get("projects") or {}).get(
            _config.os.path.abspath(cwd or _config.os.getcwd()), {}
        )
        previous_engine = previous.get("engine") if isinstance(previous, dict) else None
        if resume and previous_engine in candidates:
            chosen = candidates[previous_engine]
            reason = "resume uses this project's previous Neomax orchestrator"
        else:
            rank = {engine: i for (i, engine) in enumerate(order)}

            def score(row):
                pressure = row["pressure"]
                tier = (
                    0
                    if pressure is not None and pressure < 90
                    else 1
                    if pressure is None
                    else 2
                )
                return (
                    tier,
                    pressure if pressure is not None else 0,
                    row["live"],
                    1 if row["engine"] == previous_engine else 0,
                    rank[row["engine"]],
                )

            chosen = min(candidates.values(), key=score) if candidates else None
            if chosen:
                if chosen["pressure"] is None:
                    reason = "best eligible provider by live load and recent selection; quota percentage unavailable"
                else:
                    reason = "largest measured quota headroom among healthy eligible providers"
    if not chosen:
        return None
    return dict(
        chosen,
        engines=worker_engines,
        orchestrator_engines=list(candidates),
        reason=reason,
        priority=order,
        cwd=_config.os.path.abspath(cwd or _config.os.getcwd()),
    )


def cmd_pick_neomax(argv):
    from . import config as _config
    from . import models as _models

    opts = {
        "json": False,
        "record": False,
        "resume": False,
        "dedicated": False,
        "priority": None,
        "engine": None,
        "cwd": _config.os.getcwd(),
    }
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg in ("--json", "--record", "--resume", "--dedicated"):
            opts[arg[2:].replace("-", "_")] = True
        elif arg in ("--priority", "--engine", "--cwd"):
            if not args:
                _models.err("neomax pick-neomax: %s requires a value" % arg)
                _config.sys.exit(2)
            opts[arg[2:]] = args.pop(0)
        elif (
            arg.startswith("--priority=")
            or arg.startswith("--engine=")
            or arg.startswith("--cwd=")
        ):
            (key, value) = arg[2:].split("=", 1)
            opts[key] = value
        else:
            _models.err("neomax pick-neomax: unknown option %s" % arg)
            _config.sys.exit(2)
    choice = pick_neomax_orchestrator(
        opts["priority"], opts["engine"], opts["cwd"], opts["resume"], opts["dedicated"]
    )
    if not choice:
        _models.err(
            "neomax pick-neomax: no eligible authenticated provider. Connect at least one with cmax N, cdx login N, ocx login N, kmx login N oauth, or gmx login N."
        )
        _config.sys.exit(1)
    if opts["record"]:
        state = _neomax_selection_state()
        projects = state.setdefault("projects", {})
        projects[choice["cwd"]] = {
            "engine": choice["engine"],
            "selected_at": int(_config.time.time()),
        }
        _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
        path = _config.os.path.join(_config.STATE_DIR, "neomax-selection.json")
        tmp = path + ".tmp.%d" % _config.os.getpid()
        with open(tmp, "w") as f:
            _config.json.dump(state, f, indent=2)
        _config.os.replace(tmp, path)
    if opts["json"]:
        print(_config.json.dumps(choice, separators=(",", ":")))
    else:
        print(choice["engine"])


def cmd_orch_on(argv):
    """Print how many OTHER live orchestrators are on a given account (config-dir) — the
    launcher's co-location guard. Excludes THIS session (NEOMAX_ORCH_SESSION) so a re-launch/
    heartbeat never self-flags. Prints `0` (never errors) for an unknown dir/engine.
        neomax orch-on --dir <profile> [--engine claude]"""
    from . import models as _models
    from . import orchestrators as _orchestrators

    (engine, d) = ("claude", None)
    if "--engine" in argv:
        i = argv.index("--engine")
        if i + 1 < len(argv):
            engine = argv[i + 1]
    if "--dir" in argv:
        i = argv.index("--dir")
        if i + 1 < len(argv):
            d = argv[i + 1]
    if not d or engine not in _models.ENGINES:
        print("0")
        return
    try:
        n = len(
            _orchestrators.orchestrators_on_account(
                d, engine, exclude_session=_orchestrators.current_orch_session()
            )
        )
    except Exception:
        n = 0
    print(n)
