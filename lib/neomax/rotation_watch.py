"""Model-free rotation watcher and lifecycle hooks."""


def rotation_tick(force_active=False):
    """Model-free auto-rotation sweep — the SAFETY NET the per-turn Stop hook can't be. A session
    that has ALREADY hit its usage wall produces no turn-end, so its Stop hook never fires; but this
    poller is a SEPARATE process (the launchd usage watcher) that keeps running and rotates the
    stuck session onto a fresh account so work resumes — with no /login and no AI. Rotates:
      • the SOLO profile (99% 5h / 99% weekly), and
      • every ARMED profile (a human `/rotate --arm` OR an auto-armed orchestrator) that is over its
        own 5h/weekly threshold.
    A marker aged out past ARMED_ROTATE_AGE_S is ignored (its session is gone). A profile written to
    within ROTATE_TICK_ACTIVE_GRACE_S is skipped (it's mid-turn → the Stop hook handles it), unless
    `force_active`. Returns the number of profiles actually rotated. Never raises."""
    from . import account_auth as _account_auth
    from . import config as _config
    from . import keepalive as _keepalive
    from . import rotation as _rotation
    from . import rotation_commands as _rotation_commands
    from . import rotation_state as _rotation_state
    from . import solo as _solo

    rotated = 0
    try:
        solo = _solo._solo_profile()
        if _config.os.path.isdir(solo) and _account_auth.logged_in(solo, "claude"):
            if force_active or not _rotation_commands._profile_recently_active(
                solo, within_s=_keepalive.ROTATE_TICK_ACTIVE_GRACE_S
            ):
                msg = _solo.solo_rotate(quiet=True)
                if isinstance(msg, str) and msg.startswith("ROTATED"):
                    rotated += 1
    except Exception:
        pass
    try:
        now = _config.time.time()
        for ap, rec in list(_rotation_state._armed_rotate_map().items()):
            if (
                not isinstance(rec, dict)
                or now - rec.get("ts", 0) > _rotation_state.ARMED_ROTATE_AGE_S
            ):
                continue
            if not force_active and _rotation_commands._profile_recently_active(
                ap, within_s=_keepalive.ROTATE_TICK_ACTIVE_GRACE_S
            ):
                continue
            try:
                msg = _rotation.session_rotate(
                    profile=ap,
                    threshold=rec.get("threshold", _solo.SOLO_5H_ROTATE),
                    prefer=rec.get("prefer") or None,
                    weekly_threshold=rec.get("weekly_threshold", _solo.SOLO_7D_ROTATE),
                )
                if isinstance(msg, str) and msg.startswith("ROTATED"):
                    rotated += 1
            except Exception:
                pass
    except Exception:
        pass
    return rotated


def cmd_rotate_tick(argv):
    """Run ONE model-free rotation sweep now (what the usage watcher runs every ~30s). Useful for a
    manual kick or a cron. `--active` rotates even a mid-turn session (skip the grace guard).
       neomax rotate-tick [--active]"""
    n = rotation_tick(force_active="--active" in argv)
    print("rotate-tick: rotated %d profile(s)" % n)


def cmd_keepalive(argv):
    """Keep idle accounts logged in by triggering each near-expiry account's NATIVE token
    refresh (a minimal CLI turn) — so they stay connected whether or not you're running an
    orchestrator, exactly like an account you actively use. Runs automatically inside the
    usage-watch launchd agent; this command is for a manual/cron sweep.
       neomax keepalive [--once]"""
    from . import config as _config
    from . import keepalive as _keepalive
    from . import usage_watch as _usage_watch

    try:
        with open(_config.USAGE_WATCH_STATE) as f:
            state = _config.json.load(f)
    except (OSError, ValueError):
        state = {}
    n = _keepalive.keepalive_pass(state)
    _usage_watch._save_watch_state(state)
    print("keepalive: refreshed %d idle near-expiry account(s)" % n)


def cmd_usage_watch(argv):
    """Background watcher — capture every completion's tokens in REAL TIME into our
    append-only ledger, before the CLIs compact their logs. Runs AUTOMATICALLY (launchd
    agent) — not something the orchestrator manages. ALSO keeps idle accounts logged in
    (refreshes near-expiry tokens natively) so every account stays connected.
       neomax usage-watch              loop forever (the daemon)
       neomax usage-watch --once       a single live sweep (cron-style)
       neomax usage-watch --rebuild    wipe + re-scan ALL history with correct dates
       neomax usage-watch --no-backfill  on first run, baseline to NOW (no history)
    On first run it parses ALL history (correctly dated per completion) so 7d/30d/all-time
    are populated immediately, then captures every new completion live. The ledger accumulates
    from then on, so it stays the complete record going forward."""
    from . import config as _config
    from . import keepalive as _keepalive
    from . import models as _models
    from . import usage_ingest as _usage_ingest
    from . import usage_watch as _usage_watch

    once = "--once" in argv
    rebuild = "--rebuild" in argv
    backfill = "--no-backfill" not in argv
    try:
        with open(_config.USAGE_WATCH_STATE) as f:
            state = _config.json.load(f)
    except (OSError, ValueError):
        state = {}
    if rebuild:
        try:
            if _config.os.path.isdir(_config.USAGE_LEDGER_DIR):
                for n in _config.os.listdir(_config.USAGE_LEDGER_DIR):
                    if n.endswith(".jsonl"):
                        _config.os.remove(
                            _config.os.path.join(_config.USAGE_LEDGER_DIR, n)
                        )
        except OSError:
            pass
        state = {}
        (_, n) = _usage_watch.usage_watch_pass(state, full=True)
        state["baselined"] = True
        _usage_watch._save_watch_state(state)
        print(
            "usage-watch: rebuilt ledger from full history — %d completion record(s)"
            % n
        )
        if once:
            return
    else:
        try:
            have_ledger = any(
                (
                    f.endswith(".jsonl")
                    for f in _config.os.listdir(_config.USAGE_LEDGER_DIR)
                )
            )
        except OSError:
            have_ledger = False
        if not state.get("baselined") or (
            backfill and (not have_ledger) and _usage_ingest._usage_session_files()
        ):
            if state.get("baselined"):
                state = {}
                _models.err(
                    "usage-watch: ledger empty but transcripts exist — re-backfilling full history"
                )
            _usage_watch.usage_watch_pass(state, full=backfill, baseline=not backfill)
            state["baselined"] = True
            _usage_watch._save_watch_state(state)
    poll = float(_config.os.environ.get("NEOMAX_USAGE_POLL", "3"))
    last_ka = 0.0
    last_rot = 0.0
    while True:
        (state, n) = _usage_watch.usage_watch_pass(state)
        if _config.time.time() - last_ka >= _usage_watch.KEEPALIVE_EVERY_S:
            try:
                _keepalive.keepalive_pass(state)
            except Exception:
                pass
            last_ka = _config.time.time()
        if _config.time.time() - last_rot >= _keepalive.ROTATE_TICK_EVERY_S:
            try:
                rotation_tick()
            except Exception:
                pass
            last_rot = _config.time.time()
        _usage_watch._save_watch_state(state)
        if once:
            print("usage-watch: swept %d new completion record(s)" % n)
            return
        _config.time.sleep(poll)


def _orchestrator_turn_reminder():
    """SHORT per-turn live-fleet STATUS line for the UserPromptSubmit hook (returned as
    `additionalContext`, so the MODEL re-reads it every turn). Self-gates EXACTLY like
    `orient --hook`: emits ONLY in an interactive orchestrator session (NEOMAX_ROLE set, not a
    neomax worker, not `cmax solo`) — None otherwise.

    Deliberately informational only: a previous version
    re-asserted a DELEGATE-don't-execute mandate every turn and made the orchestrator far too
    aggressive. It reports live worker/account counts and NOTHING else — no targets, no caps, no
    mandates. The idle-account count is a FACT the orchestrator can act on (parallel work spreads
    evenly across accounts); HOW to orchestrate comes from the project's CLAUDE.md / AGENTS.md.

    Cheap + ironclad: NO network — counts in-scope profiles + active workers from the local runs
    registry; ANY error → None (a reminder must never perturb a user's message)."""
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    if (
        not _config.os.environ.get("NEOMAX_ROLE")
        or _config.os.environ.get("NEOMAX_WORKER")
        or _config.os.environ.get("NEOMAX_MODE") == "solo"
    ):
        return None
    try:
        scope = _config.allowed_engines()
        bits = []
        if "claude" in scope:
            bits.append("%d Claude" % len(_models.engine_profiles("claude")))
        if "codex" in scope:
            bits.append("%d Codex" % len(_models.engine_profiles("codex")))
        if "opencode" in scope:
            bits.append("%d OpenCode" % len(_models.engine_profiles("opencode")))
        if "kimi" in scope:
            bits.append("%d Kimi" % len(_models.engine_profiles("kimi")))
        if "grok" in scope:
            bits.append("%d Grok" % len(_models.engine_profiles("grok")))
        fleet = " + ".join(bits) or "the"
        n_acct = sum((len(_models.engine_profiles(e)) for e in scope))
        active = [
            r
            for r in _runs.all_runs()
            if _orchestrators.effective_status(r) in ("running", "orphaned")
        ]
        n_run = len(active)
        busy = len({r.get("profile") for r in active if r.get("profile")})
    except Exception:
        return None
    idle = max(0, n_acct - busy)
    return (
        "━ ORCHESTRATOR MODE — live fleet: %d worker(s) running across %d account(s); ~%d of %d in-scope account(s) idle (%s pool). `neomax status` = live auth/usage, `neomax help` = the command registry."
        % (n_run, busy, idle, n_acct, fleet)
    )


def cmd_turn_hook(argv):
    """Add local fleet context to Neomax orchestrator turns without touching model state."""
    from . import config as _config

    reminder = _orchestrator_turn_reminder()
    if reminder:
        print(
            _config.json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": reminder,
                    }
                }
            )
        )


def cmd_model_guard(argv):
    cmd_turn_hook(argv)


def cmd_usage_hook(argv):
    """Claude Code Stop-hook entrypoint — fires the instant a turn completes (event-
    driven, automatic via settings.json). Reads the hook JSON on stdin, tails the named
    transcript, and records the latest completion(s). Belt-and-suspenders with the
    watcher (same ledger, deduped). Always exits 0 (never blocks Claude)."""
    from . import codex_usage as _codex_usage
    from . import config as _config
    from . import models as _models
    from . import orchestrators as _orchestrators
    from . import rotation as _rotation
    from . import rotation_state as _rotation_state
    from . import solo as _solo
    from . import usage_ingest as _usage_ingest

    try:
        payload = _config.json.loads(_config.sys.stdin.read() or "{}")
    except (ValueError, OSError):
        payload = {}
    try:
        _orchestrators.orchestrator_heartbeat(_orchestrators.current_orch_session())
    except Exception:
        pass
    tpath = payload.get("transcript_path") or ""
    if tpath and _config.os.path.isfile(tpath):
        acct = ".claude"
        for p in _models.engine_profiles("claude"):
            if _config.os.path.abspath(tpath).startswith(
                _config.os.path.abspath(p) + _config.os.sep
            ):
                acct = _config.os.path.basename(p)
                break
        (recs, seen) = ([], set())
        try:
            tail = _codex_usage.tail_file(tpath, 256 * 1024)
            for line in tail.split("\n"):
                r = _usage_ingest._claude_records_from_line(line, acct)
                if r and r["id"] not in seen:
                    seen.add(r["id"])
                    recs.append(r)
            _usage_ingest._ledger_append(recs[-3:])
        except Exception:
            pass
    try:
        if _config.os.environ.get("NEOMAX_MODE") == "solo":
            _solo.solo_rotate()
        else:
            prof = _rotation_state._current_session_profile()
            armed = _rotation_state.armed_rotate_take(prof, payload.get("session_id"))
            if armed:
                _rotation.session_rotate(
                    profile=prof,
                    threshold=armed["threshold"],
                    prefer=armed["prefer"],
                    weekly_threshold=armed.get(
                        "weekly_threshold", _solo.SOLO_7D_ROTATE
                    ),
                )
    except Exception:
        pass
    _config.sys.exit(0)
