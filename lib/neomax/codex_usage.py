"""Codex quota parsing and run finalization."""


def read_codex_window(profile, thread_id=None):
    """Read the LATEST token_count event's rate_limits from a CODEX_HOME rollout — the
    account's current 5h/7d usage. Tail-reads only the end of the newest rollout (cheap
    even for long sessions) so the dashboard can poll it live."""
    from . import account_state as _account_state
    from . import config as _config
    from . import usage_windows as _usage_windows

    sessions = _config.os.path.join(_config.os.path.abspath(profile), "sessions")
    files = _config.globmod.glob(
        _config.os.path.join(sessions, "**", "*.jsonl"), recursive=True
    )
    if not files:
        return None
    if thread_id:
        match = [f for f in files if thread_id in f]
        if not match:
            return None
        files = match
    rollout = max(files, key=_config.os.path.getmtime)
    rl = None
    for chunk in (256 * 1024, 4 * 1024 * 1024):
        blob = tail_file(rollout, chunk)
        if not blob:
            break
        for line in reversed(blob.split("\n")):
            if '"token_count"' not in line or '"rate_limits"' not in line:
                continue
            try:
                evt = _config.json.loads(line)
            except ValueError:
                continue
            p = evt.get("payload") or evt
            if p.get("type") == "token_count" and p.get("rate_limits"):
                rl = p["rate_limits"]
                break
        if rl or len(blob) < chunk:
            break
    if not rl:
        return None
    windows = {}
    for slot in ("primary", "secondary"):
        w = rl.get(slot) or {}
        if w.get("used_percent") is None:
            continue
        mins = w.get("window_minutes")
        if mins is None:
            key = "five_hour" if slot == "primary" else "seven_day"
        else:
            key = (
                "five_hour"
                if float(mins) <= _usage_windows.CODEX_5H_WINDOW_MAX_MIN
                else "seven_day"
            )
        prev = windows.get(key)
        if prev and (prev.get("used_percent") or 0) >= (w.get("used_percent") or 0):
            continue
        windows[key] = {
            "used_percent": w.get("used_percent"),
            "resets_at": _account_state.to_epoch_seconds(w.get("resets_at")),
        }
    return {
        "five_hour": windows.get("five_hour")
        or {"used_percent": None, "resets_at": None},
        "seven_day": windows.get("seven_day")
        or {"used_percent": None, "resets_at": None},
        "source": "codex-rollout",
    }


CODEX_USAGE_FRESH_S = 12


def _normalize_codex_windows(d):
    """Reinterpret a PRE-2026-07-27 cached Codex record in place-safe fashion.

    Before Codex dropped its 5h limit, `primary` was the 5-hour window and was stored under
    "five_hour". Now `primary` IS the weekly window. A cache written before the change therefore
    holds the account's real WEEKLY usage under "five_hour" with "seven_day" empty — and since
    Codex has no 5h window any more, that number would otherwise be discarded and a genuinely
    exhausted account (observed live: one at 100%) would read as completely fresh and keep
    receiving work. When the record has a five_hour reading and NO seven_day reading, move it to
    seven_day. Idempotent, and a no-op on correctly-shaped records."""
    if not isinstance(d, dict):
        return d
    five = d.get("five_hour") or {}
    seven = d.get("seven_day") or {}
    if five.get("used_percent") is not None and seven.get("used_percent") is None:
        d = dict(d)
        d["seven_day"] = dict(five)
        d["five_hour"] = {"used_percent": None, "resets_at": None}
        d["migrated_from"] = "pre-2026-07-27-primary-was-5h"
    return d


def fetch_codex_usage(profile):
    """This Codex account's CURRENT 5h/7d usage, read LIVE from its newest rollout's
    rate_limits — so a session run OUTSIDE the harness (plain `codex`/`cdxmax N`) shows
    up on the dashboard too, not just neomax workers. Cached CODEX_USAGE_FRESH_S; on a
    miss/parse-failure the last good cache is kept (no flicker). None if no rollout yet."""
    from . import config as _config

    cache = _config.os.path.join(
        _config.USAGE_DIR, "codex-%s.json" % _config.os.path.basename(profile)
    )
    now = _config.time.time()
    cached = None
    try:
        with open(cache) as f:
            d = _normalize_codex_windows(_config.json.load(f))
        cached = d
        if now - (d.get("observed_at") or 0) < CODEX_USAGE_FRESH_S:
            return d
    except (OSError, ValueError, AttributeError):
        pass
    live = read_codex_window(profile)
    if not live or (
        (live.get("five_hour") or {}).get("used_percent") is None
        and (live.get("seven_day") or {}).get("used_percent") is None
    ):
        return cached
    live["observed_at"] = int(now)
    try:
        _config.os.makedirs(_config.USAGE_DIR, exist_ok=True)
        tmp = cache + ".tmp"
        with open(tmp, "w") as f:
            _config.json.dump(live, f)
        _config.os.rename(tmp, cache)
    except OSError:
        pass
    return live


def tail_file(path, nbytes):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - nbytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def finish(rec, status):
    from . import account_state as _account_state
    from . import config as _config
    from . import gitops as _gitops
    from . import models as _models
    from . import runs as _runs
    from . import usage_windows as _usage_windows

    rec["status"] = status
    rec["ended"] = int(_config.time.time())
    if not rec.get("killed"):
        try:
            rec["killed"] = bool((_runs.load_run(rec["id"]) or {}).get("killed"))
        except Exception:
            pass
    rec["acknowledged"] = False
    engine = rec.get("engine", "claude")
    plist = _models.engine_profiles(engine)
    acct = plist.index(rec["profile"]) + 1 if rec["profile"] in plist else "?"
    _gitops.worktree_outcome(rec)
    _runs.save_run(rec)
    _runs.archive_run(rec)
    _runs.log_event(
        rec,
        "finished",
        result_status=status,
        worktree_state=rec.get("worktree_state"),
        children=len(rec.get("children") or []),
    )
    if rec.get("result_text"):
        print(rec["result_text"], flush=True)
    _models.err(
        "NEOMAX STATUS %s run=%s engine=%s account=%s session=%s"
        % (status, rec["id"], engine, acct, rec.get("session") or "-")
    )
    if (
        status == "done"
        and rec.get("pr")
        and (rec.get("worktree_state") == "has_changes")
    ):
        _gitops.open_pr(rec, base=rec.get("base_ref"))
    if status in ("stalled", "timeout"):
        _models.err(
            "neomax: worker killed (%s). Resume with context: neomax resume %s"
            % (status, rec["id"])
        )
        _config.sys.exit(124)
    if status == "aborted":
        _models.err(
            "neomax: worker interrupted cleanly. Resume with: neomax resume %s"
            % rec["id"]
        )
        _config.sys.exit(130)
    if status == "limit":
        weekly = _usage_windows.is_weekly_limit(rec)
        when = ""
        if rec.get("resets_at"):
            ts = _account_state.to_epoch_seconds(rec["resets_at"])
            if ts:
                fmt = "%a %H:%M" if weekly else "%H:%M"
                when = " (resets %s)" % _config.time.strftime(
                    fmt, _config.time.localtime(ts)
                )
        if weekly:
            _models.err(
                "neomax: account hit its WEEKLY limit%s — it's out for ~days. ALWAYS reassign now (never wait): neomax retry %s"
                % (when, rec["id"])
            )
        else:
            _models.err(
                "neomax: account hit its 5h usage limit%s. Retry on another account: neomax retry %s"
                % (when, rec["id"])
            )
        _config.sys.exit(75)
    if status == "error":
        _models.err(
            "neomax: worker failed (%s). Retry: neomax retry %s"
            % (rec.get("error_detail", "")[:200], rec["id"])
        )
        _config.sys.exit(1)
    _config.sys.exit(0)


def maybe_cooldown(rec, status):
    """Record an account's usage-limit cooldown so future `auto` selection (and the
    portal/status) avoid it. Used by run_with_failover AND the direct resume/retry
    paths, which otherwise wouldn't cool a freshly-limited account."""
    from . import account_state as _account_state
    from . import config as _config

    if status == "limit":
        reset = _account_state.normalize_reset_epoch(rec.get("resets_at"))
        _account_state.set_cooldown(
            rec["profile"], reset or _config.time.time() + _config.DEFAULT_COOLDOWN_S
        )


def remember_session(rec):
    """Keep the prior session/thread id (and account) in a history list before a
    resume/retry/failover replaces it — so the audit trail + affinity never lose
    which sessions worked this run."""
    from . import config as _config

    sid = rec.get("session")
    if sid:
        hist = rec.setdefault("session_history", [])
        entry = {
            "session": sid,
            "account": _config.os.path.basename(rec.get("profile", "")),
            "attempt": rec.get("attempt"),
        }
        if entry not in hist:
            hist.append(entry)


CROSS_ENGINE_NOTE = "\n\nNOTE: a previous attempt by a DIFFERENT agent ran in this same working directory and hit a usage limit. Its committed work is on the current branch and partial work may be in the tree. Run `git status` + `git log --oneline -5`, inspect what exists, and CONTINUE the task to completion — do not start over or discard work."
