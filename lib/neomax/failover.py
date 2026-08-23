"""Cross-account and cross-provider worker failover."""


def _cross_engine_switch(rec, other, nxt):
    """Re-target an in-flight TASK to the other engine (sessions can't cross engines,
    but tasks can — the work lives in the worktree/branch; the new engine's worker
    continues in place). Clears engine-specific fields so worker_argv rebuilds cleanly."""
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import models as _models

    rec["engine"] = other
    rec["profile"] = nxt
    if other == "opencode":
        rec["model"] = _models.default_model("opencode")
        rec["effort"] = None
        rec["ultra"] = False
    elif other == "kimi":
        rec["model"] = _models.default_model("kimi")
        rec["effort"] = None
        rec["ultra"] = False
    elif other == "grok":
        rec["model"] = _models.default_model("grok")
        rec["effort"] = None
        rec["ultra"] = False
    elif other == "codex":
        rec["model"] = _models.default_model("codex")
        if rec.get("effort") in ("max",):
            rec["effort"] = "xhigh"
        rec["effort"] = rec.get("effort") or "high"
        rec["ultra"] = False
    else:
        rec["model"] = _models.default_model("claude")
    rec["session"] = str(_config.uuidlib.uuid4()) if other == "claude" else None
    rec["_prompt_to_send"] = rec["prompt"] + _codex_usage.CROSS_ENGINE_NOTE


def cross_engine_order(engine):
    """Prefer the free OX pool when leaving a paid engine; never leave fleet scope."""
    from . import config as _config

    order = {
        "claude": ("opencode", "grok", "kimi", "codex"),
        "codex": ("opencode", "grok", "kimi", "claude"),
        "opencode": ("grok", "kimi", "codex", "claude"),
        "kimi": ("opencode", "grok", "codex", "claude"),
        "grok": ("opencode", "kimi", "codex", "claude"),
    }.get(engine, ())
    return [candidate for candidate in order if _config.engine_in_scope(candidate)]


def run_with_failover(rec):
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import models as _models
    from . import runs as _runs
    from . import selection as _selection
    from . import worker_commands as _worker_commands

    tried = rec.setdefault("tried", [])
    while True:
        engine = rec.get("engine", "claude")
        tried.append(rec["profile"])
        rec["status"] = "running"
        rec["pid"] = _config.os.getpid()
        _runs.save_run(rec)
        status = _worker_commands.execute(rec)
        _codex_usage.maybe_cooldown(rec, status)
        total_pool = sum(
            (len(_models.worker_profiles(e)) for e in _config.allowed_engines())
        )
        if (
            status in ("limit", "error")
            and (not rec.get("no_failover"))
            and (not rec.get("resumed"))
            and (rec["attempt"] < total_pool)
        ):
            try:
                nxt = _selection.pick_account("auto", exclude=set(tried), engine=engine)
            except SystemExit:
                nxt = None
            if nxt is None and status == "limit":
                for other in cross_engine_order(engine):
                    try:
                        nxt = _selection.pick_account(
                            "auto", exclude=set(tried), engine=other
                        )
                    except SystemExit:
                        nxt = None
                    if nxt is not None:
                        _models.err(
                            "neomax: %s pool exhausted (%s) — CROSS-ENGINE continue on %s %s (attempt %d)"
                            % (
                                engine,
                                status,
                                other,
                                _config.os.path.basename(nxt),
                                rec["attempt"] + 1,
                            )
                        )
                        _runs.log_event(
                            rec,
                            "cross_engine_failover",
                            reason=status,
                            from_engine=engine,
                            to_engine=other,
                            to=_config.os.path.basename(nxt),
                        )
                        _codex_usage.remember_session(rec)
                        _cross_engine_switch(rec, other, nxt)
                        rec["attempt"] += 1
                        break
                if nxt is not None:
                    continue
            if nxt is None:
                _codex_usage.finish(rec, status)
            _models.err(
                "neomax: %s on account %s — failing over to %s (attempt %d)"
                % (status, rec["profile"], nxt, rec["attempt"] + 1)
            )
            _runs.log_event(
                rec, "failover", reason=status, to=_config.os.path.basename(nxt)
            )
            _codex_usage.remember_session(rec)
            rec["profile"] = nxt
            rec["attempt"] += 1
            rec["session"] = (
                str(_config.uuidlib.uuid4()) if engine == "claude" else None
            )
            rec["_prompt_to_send"] = rec["prompt"] + _models.CONTINUATION_NOTE
            continue
        _codex_usage.finish(rec, status)
