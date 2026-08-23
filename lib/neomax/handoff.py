"""Interactive orchestrator handoff across profiles."""


def orchestrator_engine():
    """Which engine THIS interactive orchestrator is — from NEOMAX_ROLE the launcher sets
    ('claude' via cmax, 'codex' via cdxmax, 'opencode' via ocmax, 'kimi' via kmax,
    'grok' via gmax).
    Falls back to inferring from the env
    (CODEX_HOME set + no CLAUDE_CONFIG_DIR → codex) else claude."""
    from . import config as _config

    role = (_config.os.environ.get("NEOMAX_ROLE") or "").strip().lower()
    if role in ("claude", "codex", "opencode", "kimi", "grok"):
        return role
    if _config.os.environ.get("GROK_HOME") and (
        not _config.os.environ.get("CLAUDE_CONFIG_DIR")
    ):
        return "grok"
    if _config.os.environ.get("KIMI_CODE_HOME") and (
        not _config.os.environ.get("CLAUDE_CONFIG_DIR")
    ):
        return "kimi"
    if _config.os.environ.get("CODEX_HOME") and (
        not _config.os.environ.get("CLAUDE_CONFIG_DIR")
    ):
        return "codex"
    return "claude"


def current_orchestrator_profile(engine=None):
    """The account THIS interactive orchestrator session is running as, from its config
    env (CLAUDE_CONFIG_DIR for claude / CODEX_HOME for codex; unset → that engine's
    default dir). A bash tool call inside the orchestrator inherits the session env."""
    from . import config as _config
    from . import models as _models

    engine = engine or orchestrator_engine()
    cfg = _config.os.environ.get(_models.ENGINES[engine]["config_env"])
    return (
        _config.os.path.abspath(cfg) if cfg else _models.ENGINES[engine]["default_dir"]
    )


def pick_freshest_account(engine, exclude=()):
    """The best account OF THIS ENGINE to hand the orchestrator off to. A landing spot must have REAL
    headroom (not cooled, 5h < FIVE_H_SKIP, weekly < SEVEN_D_SKIP) so we don't rotate onto a
    near-maxed account and ping-pong. Among those, pick_least_loaded: 5h-FIRST (lowest 5h) with an
    anti-herd claim so two orchestrators handing off at once spread, and a gentle weekly-deadline
    nudge. None when no account has headroom → caller advises lean-on-other-engine / pause."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import models as _models
    from . import selection_claims as _selection_claims
    from . import usage_windows as _usage_windows

    for weekly_cap in (_usage_windows.SEVEN_D_SKIP, _usage_windows.SEVEN_D_HARD):
        cands = []
        for p in _models.engine_profiles(engine):
            if (
                p in exclude
                or not _account_auth.logged_in(p, engine)
                or _account_state.is_paused(p)
            ):
                continue
            if (
                _account_state.cooling_down(p)
                or _usage_windows.usage_window(p, engine) >= _usage_windows.FIVE_H_SKIP
                or _usage_windows.weekly_window(p, engine) >= weekly_cap
            ):
                continue
            cands.append(p)
        if cands:
            return _selection_claims.pick_least_loaded(cands, engine)
    return None


def cmd_handoff(argv):
    """Orchestrator self-rotation — keep the interactive orchestrator alive across usage
    walls WITHOUT losing work. Works for whichever engine is orchestrating and rotates
    within that SAME engine (an interactive session cannot switch engines in place).
       neomax handoff --check [--json]   report THIS account's 5h/7d + whether a
                                            rotation is advised (exit 10 if advised).
       neomax handoff [--to ACCOUNT] [--prompt "..."]
                                        hand off NOW: launch a fresh orchestrator on
                                            the freshest same-engine account (auto-
                                            resumes); --dry-run prints the plan only.
    A running session can't swap its own auth in place (account bound at launch), so
    'rotation' = a clean handoff to a new session on a fresher account. ALL work is
    durable (STATUS.md + ledger + git branches + inbox) → the new orchestrator resumes
    with nothing lost."""
    from . import account_auth as _account_auth
    from . import account_state as _account_state
    from . import config as _config
    from . import credentials as _credentials
    from . import mode_catalog as _mode_catalog
    from . import models as _models
    from . import quota_deadlines as _quota_deadlines
    from . import rotation_commands as _rotation_commands
    from . import runs as _runs
    from . import selection as _selection
    from . import status_helpers as _status_helpers
    from . import usage_windows as _usage_windows

    want_json = "--json" in argv
    dry = "--dry-run" in argv
    prompt = None
    if "--prompt" in argv:
        i = argv.index("--prompt")
        if i + 1 < len(argv):
            prompt = argv[i + 1]
    engine = orchestrator_engine()
    if "--engine" in argv:
        i = argv.index("--engine")
        if i + 1 >= len(argv) or argv[i + 1] not in _models.ENGINES:
            _models.err(
                "neomax handoff: --engine must be claude, codex, opencode, kimi, or grok"
            )
            _config.sys.exit(2)
        engine = argv[i + 1]
    requested = []
    for i, value in enumerate(argv):
        if value == "--to" and i + 1 < len(argv):
            requested.append(argv[i + 1])
    reason_override = None
    if "--reason" in argv:
        i = argv.index("--reason")
        if i + 1 < len(argv):
            reason_override = argv[i + 1]
    other = "the other worker pools"
    launcher = {
        "claude": "cmax",
        "codex": "cdxmax",
        "opencode": "ocmax",
        "kimi": "kmax",
        "grok": "gmax",
    }[engine]
    me = current_orchestrator_profile(engine)
    acct_no = _status_helpers.profile_acct_no(me, engine)
    five = _usage_windows.usage_window(me, engine)
    seven = _usage_windows.weekly_window(me, engine)
    (advised, reason) = _usage_windows.rotate_advice(five, seven, engine)
    reason = reason_override or reason
    target = None
    if requested:
        for selector in _rotation_commands._parse_account_selectors(requested):
            candidate = _credentials._resolve_profile(selector, engine)
            if (
                not candidate
                or _config.os.path.abspath(candidate) == _config.os.path.abspath(me)
                or (not _account_auth.logged_in(candidate, engine))
                or _account_state.is_paused(candidate)
                or _account_state.cooling_down(candidate)
                or (
                    _usage_windows.usage_window(candidate, engine)
                    >= _usage_windows.FIVE_H_SKIP
                )
                or (
                    _usage_windows.weekly_window(candidate, engine)
                    >= _usage_windows.SEVEN_D_HARD
                )
            ):
                continue
            target = candidate
            break
        if not target:
            _models.err(
                "neomax handoff: requested %s account(s) [%s] are unavailable, current, paused, cooled, or at the usage wall"
                % (engine, ", ".join((str(x) for x in requested)))
            )
            _config.sys.exit(1)
    else:
        target = pick_freshest_account(engine, exclude={me})
    target_no = _status_helpers.profile_acct_no(target, engine) if target else None
    target_reset = (
        _selection._reset_eta(_quota_deadlines.weekly_reset_at(target, engine))
        if target
        else ""
    )
    if "--check" in argv:
        info = {
            "engine": engine,
            "account": acct_no,
            "five_hour": round(five, 1),
            "seven_day": round(seven, 1),
            "advised": advised,
            "reason": reason,
            "target_account": target_no,
            "target_weekly_resets": target_reset or None,
            "target_email": _status_helpers.account_identity(target, engine).get(
                "email"
            )
            if target
            else None,
        }
        if want_json:
            print(_config.json.dumps(info))
        else:
            print(
                "orchestrator = %s account %s · 5h %.0f%% · 7d %.0f%% → %s"
                % (
                    engine,
                    acct_no,
                    five,
                    seven,
                    "ROTATE ADVISED (%s)" % reason if advised else "ok",
                )
            )
            if advised and target_no:
                print(
                    "  → hand off to account %s (soonest weekly reset%s). Run: neomax handoff"
                    % (target_no, ", " + target_reset if target_reset else "")
                )
            elif advised:
                print(
                    "  → no fresher %s account available — lean on %s / pause."
                    % (engine, other)
                )
        _config.sys.exit(10 if advised else 0)
    if not target:
        _models.err(
            "neomax handoff: no other logged-in %s account with headroom to hand off to. Lean on %s, or pause until a 5h window resets."
            % (engine, other)
        )
        _config.sys.exit(1)
    kickoff = (
        prompt
        or "You are now THE Neomax orchestrator (the previous one rotated off %s account %s near its usage limit). Resume immediately with ZERO loss: read this project's %s, then run `neomax ls` to adopt every in-flight/inbox worker, read the program's STATUS.md, and continue the execution loop exactly where it left off."
        % (
            engine,
            acct_no,
            "AGENTS.md"
            if engine in ("codex", "opencode", "kimi", "grok")
            else "CLAUDE.md",
        )
    )
    kickoff = " ".join(kickoff.split())
    target_dir = _config.os.getcwd()
    baton = {
        "ts": int(_config.time.time()),
        "engine": engine,
        "from_account": acct_no,
        "to_account": target_no,
        "reason": reason,
        "cwd": target_dir,
        "five_hour": round(five, 1),
        "seven_day": round(seven, 1),
    }
    try:
        _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
        with open(_config.os.path.join(_config.STATE_DIR, "handoff.json"), "w") as f:
            _config.json.dump(baton, f)
    except OSError:
        pass
    _runs.log_event(
        {"id": "orchestrator", "engine": engine},
        "handoff",
        from_account=acct_no,
        to_account=target_no,
        reason=reason,
    )
    if dry:
        print(
            "DRY-RUN handoff: %s account %s (%s) → account %s (%s)"
            % (
                engine,
                acct_no,
                reason,
                target_no,
                _status_helpers.account_identity(target, engine).get("email") or "?",
            )
        )
        print(
            "  would launch a fresh %s orchestrator (%s %s) in %s and wind this down."
            % (engine, launcher, target_no, target_dir)
        )
        return
    launched = False
    model_flags = []
    for eng in ("claude", "codex", "opencode", "kimi", "grok"):
        value = _config.os.environ.get("NEOMAX_%s_MODEL" % eng.upper())
        if value:
            model_flags += ["--%s-model" % eng, value]
    account_args = ["--orchestrator"] if target_no == "orch" else [str(target_no)]
    scope = (_config.os.environ.get("NEOMAX_FLEET") or "").strip()
    scope_args = ["--workers", scope] if scope else []
    launch_args = " ".join(
        (
            _mode_catalog._shell_quote(x)
            for x in account_args + scope_args + model_flags + [kickoff]
        )
    )
    shell_cmd = "cd %s && %s %s" % (
        _mode_catalog._shell_quote(target_dir),
        launcher,
        launch_args,
    )
    headless = bool(
        _config.os.environ.get("SSH_CONNECTION") or _config.os.environ.get("SSH_TTY")
    )
    if _config.sys.platform == "darwin" and (not headless):
        script = 'tell app "Terminal" to do script "%s"' % _osa_escape(shell_cmd)
        try:
            r = _config.subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=15
            )
            launched = r.returncode == 0
        except (FileNotFoundError, OSError, _config.subprocess.TimeoutExpired):
            launched = False
    print(
        "neomax: ROTATED orchestrator → %s account %s (%s). Reason: %s."
        % (
            engine,
            target_no,
            _status_helpers.account_identity(target, engine).get("email") or "?",
            reason,
        )
    )
    if launched:
        print(
            "  A new Terminal is starting the orchestrator on account %s — it resumes from STATUS.md + `neomax ls` (nothing lost)."
            % target_no
        )
        print(
            "  THIS session: stop launching new work; finish/return your current thought, then exit. The new window is the orchestrator now."
        )
    else:
        print("  Could not auto-open a Terminal. Start the new orchestrator yourself:")
        print("    %s" % shell_cmd)


def _osa_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')
