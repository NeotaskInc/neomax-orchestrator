"""Provider quota windows and hard-wall policy."""

from . import config as _config


def fetch_claude_usage(profile):
    """This Claude account's CURRENT 5h/7d usage from the OAuth usage API, fetched
    with the account's OWN token — so the number is always attributable to the right
    account. Per-account cache in USAGE_DIR: returned as-is while fresh (USAGE_FRESH_S);
    on a TRANSIENT fetch failure the last cached reading is served up to USAGE_STALE_OK_S
    old (so a momentary timeout / token-refresh 401 / network blip doesn't flicker the
    dashboard to 'unknown'). Returns {"five_hour":{used_percent,resets_at},"seven_day":
    {...},"source":"claude-api","observed_at":epoch[,"stale":True]} or None only when
    there's no token AND no usable cache. Always this account's OWN number. Never raises."""
    from . import account_auth as _account_auth
    from . import config as _config
    from . import http_client as _http_client

    cache = _config.os.path.join(
        _config.USAGE_DIR, "claude-%s.json" % _config.os.path.basename(profile)
    )
    now = _config.time.time()
    acct_uuid = _http_client._claude_local_identity(profile).get("uuid") or ""
    cached = None
    try:
        with open(cache) as f:
            d = _config.json.load(f)
        if (
            d.get("source") == "claude-api"
            and d.get("observed_at")
            and (not acct_uuid or d.get("acct_uuid", acct_uuid) == acct_uuid)
        ):
            cached = d
            if now - d["observed_at"] < _http_client.USAGE_FRESH_S:
                return d
    except (OSError, ValueError, AttributeError):
        pass

    def fallback():
        if cached and now - cached["observed_at"] < _http_client.USAGE_STALE_OK_S:
            if now - cached["observed_at"] > _http_client.USAGE_STALE_LABEL_S:
                return dict(cached, stale=True)
            return dict(cached)
        return None

    token = _http_client.claude_oauth_token(profile)
    if not token:
        token = _http_client.claude_refresh_token(profile)
    if not token:
        has_refresh = False
        try:
            blob = None
            svc = _account_auth.keychain_service(profile)
            for extra in (
                [["-a", _config.os.environ.get("USER")]]
                if _config.os.environ.get("USER")
                else []
            ) + [[]]:
                r = _config.subprocess.run(
                    ["security", "find-generic-password", "-s", svc] + extra + ["-w"],
                    capture_output=True,
                    text=True,
                    timeout=_http_client.USAGE_HTTP_TIMEOUT_S,
                )
                if r.returncode == 0 and r.stdout.strip():
                    blob = r.stdout.strip()
                    break
            if blob is None:
                try:
                    blob = open(
                        _config.os.path.join(profile, ".credentials.json")
                    ).read()
                except OSError:
                    blob = None
            if blob:
                has_refresh = bool(
                    ((_config.json.loads(blob) or {}).get("claudeAiOauth") or {}).get(
                        "refreshToken"
                    )
                )
        except Exception:
            pass
        return (
            {"expired": True, "recoverable": has_refresh}
            if cached or has_refresh
            else None
        )
    if cached and cached.get("rl_until", 0) > now:
        return fallback()
    d = None
    for attempt in (1, 2):
        try:
            d = _http_client.http_get_json(
                _http_client.CLAUDE_USAGE_URL,
                {
                    "Authorization": "Bearer " + token,
                    "anthropic-beta": "oauth-2025-04-20",
                    "anthropic-version": "2023-06-01",
                },
                _http_client.USAGE_HTTP_TIMEOUT_S,
            )
            break
        except Exception as e:
            if getattr(e, "code", None) == 429:
                if cached:
                    try:
                        with open(cache, "w") as f:
                            _config.json.dump(
                                dict(
                                    cached,
                                    rl_until=now + _http_client.USAGE_RL_BACKOFF_S,
                                ),
                                f,
                            )
                    except OSError:
                        pass
                return fallback()
            if attempt == 1:
                _config.time.sleep(0.4)
                continue
            return fallback()
    if not isinstance(d, dict):
        return fallback()

    def norm(w):
        w = w if isinstance(w, dict) else {}
        return {
            "used_percent": w.get("utilization"),
            "resets_at": _http_client.iso_to_epoch(w.get("resets_at")),
        }

    out = {
        "five_hour": norm(d.get("five_hour")),
        "seven_day": norm(d.get("seven_day")),
        "source": "claude-api",
        "observed_at": int(now),
        "acct_uuid": acct_uuid,
    }
    if (
        out["five_hour"]["used_percent"] is None
        and out["seven_day"]["used_percent"] is None
    ):
        return fallback()
    try:
        _config.os.makedirs(_config.USAGE_DIR, exist_ok=True)
        tmp = cache + ".tmp"
        with open(tmp, "w") as f:
            _config.json.dump(out, f)
        _config.os.rename(tmp, cache)
    except OSError:
        pass
    return out


def fetch_engine_usage(profile, engine):
    """Best-known provider window telemetry, or None when the provider exposes none.

    OpenCode Go currently advertises the OX model as unlimited but does not expose a
    documented per-account usage-window endpoint. Never guess a zero-valued window or
    accidentally query another provider on its behalf; reactive 429 handling owns its
    cooldown until OpenCode publishes real quota telemetry.
    """
    from . import codex_usage as _codex_usage

    if engine == "claude":
        return fetch_claude_usage(profile)
    if engine == "codex":
        return _codex_usage.fetch_codex_usage(profile)
    return None


def usage_window(profile, engine):
    """Best-known 5h-window usage % for an account (proactive selection). Claude:
    per-account OAuth usage API (cached USAGE_FRESH_S); codex: the unified usage
    cache. 0.0 when unknown (treat as fresh) or window already reset.

    An engine with NO 5h window (Codex) always reads 0.0 — enforced HERE, at the source, so
    every 5h gate downstream (selection skip, rotation trigger, landing ceiling, ranking) goes
    inert for that engine automatically and a stale pre-change cache can't resurrect a phantom
    5h number. Weekly is unaffected: see weekly_window()."""
    from . import account_state as _account_state
    from . import config as _config

    if not engine_has_5h(engine):
        return 0.0
    d = fetch_engine_usage(profile, engine)
    if d is None:
        return 0.0
    if d.get("expired"):
        return 100.0
    five = d.get("five_hour") or {}
    resets = _account_state.to_epoch_seconds(five.get("resets_at"))
    if resets and _config.time.time() >= resets:
        return 0.0
    return float(five.get("used_percent") or 0.0)


def weekly_window(profile, engine):
    """Best-known 7-DAY-window usage % for an account (so selection can spread weekly
    load + lean off a fleet that's near its weekly cap). Same source as usage_window;
    0.0 when unknown or already reset."""
    from . import account_state as _account_state
    from . import config as _config

    d = fetch_engine_usage(profile, engine)
    if d is None or d.get("expired"):
        return 0.0
    sev = d.get("seven_day") or {}
    resets = _account_state.to_epoch_seconds(sev.get("resets_at"))
    if resets and _config.time.time() >= resets:
        return 0.0
    return float(sev.get("used_percent") or 0.0)


def is_weekly_limit(rec):
    """True when this run's usage-limit hit was the 7-day/weekly window (not the 5h).
    A weekly limit puts the account out for ~days → ALWAYS reassign, never wait."""
    w = (rec.get("limit_window") or "").lower()
    return "seven" in w or "week" in w or "7d" in w or ("day" in w)


FIVE_H_SKIP = 92.0
FIVE_H_HARD = 99.0
SEVEN_D_SKIP = 99.0
SEVEN_D_HARD = 99.0
ORCH_5H_ROTATE = 99.0
ORCH_7D_ROTATE = 99.0
ENGINE_HAS_5H = {
    "claude": True,
    "codex": False,
    "opencode": False,
    "kimi": False,
    "grok": False,
}
CODEX_5H_WINDOW_MAX_MIN = 360.0


def engine_has_5h(engine):
    """Does this engine expose a 5-hour rolling window at all? False for Codex since it
    dropped the 5h limit — every 5h gate (selection skip, rotation trigger, landing ceiling)
    must then be inert for that engine rather than acting on a phantom number."""
    return ENGINE_HAS_5H.get(engine, True)


def rotate_advice(five, seven, engine):
    """(advised, reason) for a session on `engine` at these window percentages — the ONE
    place the rotation policy is decided, so handoff, the status dashboard, and the tests
    can never disagree. Claude: 5h ≥99% OR weekly ≥99%. Codex: weekly ≥99% only."""
    has5 = engine_has_5h(engine)
    if has5 and five >= ORCH_5H_ROTATE:
        return (True, "5h %.0f%% ≥ %.0f%%" % (five, ORCH_5H_ROTATE))
    if seven >= ORCH_7D_ROTATE:
        return (True, "weekly %.0f%% ≥ %.0f%%" % (seven, ORCH_7D_ROTATE))
    if has5:
        return (False, "headroom ok (5h %.0f%%, 7d %.0f%%)" % (five, seven))
    return (False, "headroom ok (weekly %.0f%%; %s has no 5h window)" % (seven, engine))


LIVE_SPREAD_WEIGHT = float(_config.os.environ.get("NEOMAX_LIVE_SPREAD", "6"))
LIVE_CONCURRENCY_CAP = int(_config.os.environ.get("NEOMAX_LIVE_CAP", "10"))
FANOUT_LANES_PER_ACCT = int(_config.os.environ.get("NEOMAX_LANES_PER_ACCT", "6"))


def default_fanout_cap():
    """Default run-all concurrency: lanes-per-account across the logged-in fleet, clamped to the
    global agent budget, but never below the raw account count (so a tiny fleet still fans out one
    part per account). Returns >=1. Pure read of the live profiles; never raises."""
    from . import account_auth as _account_auth
    from . import models as _models
    from . import queue as _queue

    accts = sum(
        (
            len(
                [a for a in _models.engine_profiles(e) if _account_auth.logged_in(a, e)]
            )
            for e in ("claude", "codex", "opencode", "kimi", "grok")
        )
    )
    if accts <= 0:
        return 1
    budget = max(1, int(_queue.AGENT_BUDGET_DEFAULT))
    cap = max(accts, min(accts * max(1, FANOUT_LANES_PER_ACCT), budget))
    return min(cap, FLEET_CONCURRENCY_CAP)


FLEET_CONCURRENCY_CAP = int(_config.os.environ.get("NEOMAX_FLEET_CAP", "50"))


def fleet_live_workers():
    """Count of dispatched workers currently live across the whole fleet (both engines, all accounts)
    — the quantity FLEET_CONCURRENCY_CAP bounds. Registry-based (precise for workers; excludes the
    orchestrator's own interactive sessions). Pure read; never raises."""
    from . import orchestrators as _orchestrators
    from . import runs as _runs

    n = 0
    try:
        for rec in _runs.all_runs():
            if rec.get("status") == "running" and _orchestrators.worker_alive(rec):
                n += 1
    except Exception:
        pass
    return n
