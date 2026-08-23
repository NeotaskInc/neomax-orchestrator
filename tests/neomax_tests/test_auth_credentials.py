"""Account identity, OAuth refresh, TLS, and explicit credential rotation."""

from .support import *


def test_account_identity_robustness():
    print("test_account_identity_robustness")
    saved = (
        m.claude_oauth_token,
        m.http_get_json,
        m.USAGE_DIR,
        m._claude_local_identity,
        m.claude_refresh_token,
        m.keychain_service,
    )
    try:
        m.claude_refresh_token = lambda p, force=False: None
        with tempfile.TemporaryDirectory() as st:
            m.USAGE_DIR = os.path.join(st, "usage")
            os.makedirs(m.USAGE_DIR)
            prof = "/h/.claude-acct2"
            cache = os.path.join(m.USAGE_DIR, "claude-.claude-acct2.json")
            m._claude_local_identity = lambda p: {
                "email": "a@x",
                "name": "A",
                "uuid": "UUID-A",
            }
            m.claude_oauth_token = lambda p: "tokA"
            m.http_get_json = lambda *a, **k: {
                "five_hour": {"utilization": 40, "resets_at": None},
                "seven_day": {"utilization": 10, "resets_at": None},
            }
            w = m.fetch_claude_usage(prof)
            check(
                w
                and w["five_hour"]["used_percent"] == 40
                and (w["acct_uuid"] == "UUID-A"),
                "usage cached tagged with the logged-in account's uuid",
            )
            m._claude_local_identity = lambda p: {
                "email": "b@y",
                "name": "B",
                "uuid": "UUID-B",
            }
            m.http_get_json = lambda *a, **k: {
                "five_hour": {"utilization": 5, "resets_at": None},
                "seven_day": {"utilization": 2, "resets_at": None},
            }
            w2 = m.fetch_claude_usage(prof)
            check(
                w2
                and w2["five_hour"]["used_percent"] == 5
                and (w2["acct_uuid"] == "UUID-B"),
                "account change invalidates the prior account's cached usage",
            )

            def boom(*a, **k):
                raise RuntimeError("timeout")

            m.http_get_json = boom
            fresh = m.USAGE_FRESH_S
            try:
                m.USAGE_FRESH_S = 0
                w3 = m.fetch_claude_usage(prof)
            finally:
                m.USAGE_FRESH_S = fresh
            check(
                w3 and w3["five_hour"]["used_percent"] == 5 and (not w3.get("stale")),
                "transient failure (valid token) -> serves last-good, never unknown; no stale label while the reading is recent (debounced)",
            )
            m.claude_oauth_token = lambda p: None
            m.keychain_service = lambda p: "svc"
            fresh = m.USAGE_FRESH_S
            try:
                m.USAGE_FRESH_S = 0
                w4 = m.fetch_claude_usage(prof)
            finally:
                m.USAGE_FRESH_S = fresh
            check(
                isinstance(w4, dict) and w4.get("expired"),
                "dead token -> expired marker (prompt re-login), not stale/unknown",
            )
            m.USAGE_FRESH_S = 0
            check(
                m.usage_window(prof, "claude") == 100.0,
                "expired account skipped in selection",
            )
            m.USAGE_FRESH_S = fresh
    finally:
        (
            m.claude_oauth_token,
            m.http_get_json,
            m.USAGE_DIR,
            m._claude_local_identity,
            m.claude_refresh_token,
            m.keychain_service,
        ) = saved


def test_oauth_refresh():
    """An IDLE Claude account whose access token expired is REFRESHED in place (the same
    refresh-token exchange the live CLI does) instead of showing 'token expired' forever:
    new access+rotated refresh persisted to the cred blob, expiry pushed out, debounced per
    account, skipped while a live session owns the refresh, and a failed exchange is a no-op."""
    print("test_oauth_refresh")
    import time as _t

    saved = {
        k: getattr(m, k)
        for k in (
            "http_post_json",
            "write_claude_cred_blob",
            "_profile_recently_active",
            "USAGE_DIR",
        )
    }
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, ".claude-acct9")
        os.makedirs(prof)
        m.USAGE_DIR = os.path.join(td, "usage")
        os.makedirs(m.USAGE_DIR)
        credp = os.path.join(prof, ".credentials.json")

        def write_blob(expat, at="OLD", rt="R1"):
            open(credp, "w").write(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": at,
                            "refreshToken": rt,
                            "expiresAt": expat,
                            "subscriptionType": "max",
                        }
                    }
                )
            )

        write_blob(int((_t.time() - 100) * 1000))
        sent = {}
        m.http_post_json = lambda url, payload, headers, timeout: (
            sent.update(url=url, payload=payload, ua=headers.get("User-Agent"))
            or {"access_token": "NEW", "refresh_token": "R2", "expires_in": 3600}
        )
        m.write_claude_cred_blob = lambda p, blob: (
            open(os.path.join(p, ".credentials.json"), "w").write(blob) or True
        )
        m._profile_recently_active = lambda p, within_s=180: False
        try:
            tok = m.claude_refresh_token(prof)
            check(
                tok == "NEW", "expired idle account -> refreshed to a new access token"
            )
            check(
                sent["url"].endswith("/v1/oauth/token")
                and "platform.claude.com" in sent["url"],
                "refresh hits the platform.claude.com token endpoint (not console.anthropic.com)",
            )
            check(
                sent["payload"]["grant_type"] == "refresh_token"
                and sent["payload"]["refresh_token"] == "R1"
                and sent["payload"].get("client_id"),
                "refresh sends grant_type=refresh_token + the stored refresh token + client_id",
            )
            check(
                sent["ua"] and "urllib" not in sent["ua"].lower(),
                "refresh sends a real User-Agent (the default urllib UA is Cloudflare-blocked)",
            )
            blob = json.loads(open(credp).read())["claudeAiOauth"]
            check(
                blob["accessToken"] == "NEW" and blob["refreshToken"] == "R2",
                "new access + ROTATED refresh token persisted to the cred blob",
            )
            check(
                m.to_epoch_seconds(blob["expiresAt"]) > _t.time(),
                "expiry pushed into the future",
            )
            check(
                blob.get("subscriptionType") == "max",
                "the rest of the blob is preserved",
            )
            write_blob(int((_t.time() - 100) * 1000), at="OLD2")
            check(
                m.claude_refresh_token(prof) is None,
                "a second attempt within the cooldown is debounced",
            )
            check(
                m.claude_refresh_token(prof, force=True) == "NEW",
                "force=True bypasses the debounce",
            )
            m._profile_recently_active = lambda p, within_s=180: True
            os.remove(
                os.path.join(m.USAGE_DIR, "refresh-%s.json" % os.path.basename(prof))
            )
            check(
                m.claude_refresh_token(prof) is None,
                "an account with a live session is skipped",
            )
            m._profile_recently_active = lambda p, within_s=180: False
            open(credp, "w").write(
                json.dumps({"claudeAiOauth": {"accessToken": "X", "expiresAt": 1}})
            )
            check(
                m.claude_refresh_token(prof, force=True) is None,
                "no refresh token -> no attempt",
            )
            write_blob(int((_t.time() - 100) * 1000))

            def boom(*a, **k):
                raise OSError("network down")

            m.http_post_json = boom
            check(
                m.claude_refresh_token(prof, force=True) is None
                and json.loads(open(credp).read())["claudeAiOauth"]["accessToken"]
                == "OLD",
                "a failed exchange is a no-op (old creds untouched)",
            )
        finally:
            for k, v in saved.items():
                setattr(m, k, v)


def test_ssl_context_has_trust_store():
    """The usage/refresh HTTPS fetches must verify against a real CA bundle. On the
    python.org macOS framework build the DEFAULT ssl context loads ZERO CAs (no
    cert.pem at its default path) → every fetch dies CERTIFICATE_VERIFY_FAILED and
    the whole usage board silently reads 'unknown'. ssl_context() must repair that
    by falling back to certifi, so the returned context always trusts >0 CAs."""
    saved = m._SSL_CTX
    try:
        m._SSL_CTX = None
        ctx = m.ssl_context()
        ca = ctx.cert_store_stats().get("x509_ca", 0)
        check(ca > 0, "ssl_context() trusts a non-empty CA store (%d certs)" % ca)
        check(m.ssl_context() is ctx, "ssl_context() is cached (one context reused)")
    finally:
        m._SSL_CTX = saved


def test_rotate_auth():
    """In-place auth rotation: copies an EXISTING valid blob into the dest profile
    (file store), updates the display identity, backs up the displaced credentials,
    logs the event, refuses same-account, and restores. Pure copy — no OAuth calls."""
    print("test_rotate_auth")
    import time as _t

    saved = (
        m.STATE_DIR,
        m.AUTH_BACKUPS,
        m.AUTH_ROTATIONS,
        m.USAGE_DIR,
        m.write_claude_cred_blob,
        m.read_claude_cred_blob,
        os.environ.get("NEOMAX_PROFILES"),
    )
    try:
        with tempfile.TemporaryDirectory() as st:
            m.STATE_DIR = st
            m.AUTH_BACKUPS = os.path.join(st, "auth-backups")
            m.AUTH_ROTATIONS = os.path.join(st, "auth-rotations.jsonl")
            m.USAGE_DIR = os.path.join(st, "usage")
            os.makedirs(m.USAGE_DIR)
            src = os.path.join(st, "src")
            dst = os.path.join(st, "dst")
            os.makedirs(src)
            os.makedirs(dst)
            os.environ["NEOMAX_PROFILES"] = src + ":" + dst
            blob_a = json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "tokA",
                        "refreshToken": "rA",
                        "expiresAt": int(_t.time() + 9999) * 1000,
                    }
                }
            )
            blob_b = json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "tokB",
                        "refreshToken": "rB",
                        "expiresAt": int(_t.time() + 9999) * 1000,
                    }
                }
            )
            open(os.path.join(src, ".credentials.json"), "w").write(blob_a)
            open(os.path.join(dst, ".credentials.json"), "w").write(blob_b)
            json.dump(
                {"oauthAccount": {"accountUuid": "U-A", "emailAddress": "a@x"}},
                open(os.path.join(src, ".claude.json"), "w"),
            )
            json.dump(
                {"oauthAccount": {"accountUuid": "U-B", "emailAddress": "b@x"}},
                open(os.path.join(dst, ".claude.json"), "w"),
            )
            m.write_claude_cred_blob = lambda prof, blob: (
                open(os.path.join(prof, ".credentials.json"), "w").write(blob) or True
            )

            def _read_blob_file_only(prof):
                try:
                    with open(os.path.join(prof, ".credentials.json")) as f:
                        return f.read().strip()
                except OSError:
                    return None

            m.read_claude_cred_blob = _read_blob_file_only
            m.cmd_rotate_auth([dst, "--from", src, "--reason", "test"])
            check(
                json.load(open(os.path.join(dst, ".credentials.json")))[
                    "claudeAiOauth"
                ]["accessToken"]
                == "tokA",
                "dest now carries the SOURCE account's tokens",
            )
            check(
                m._read_oauth_account(dst)["emailAddress"] == "a@x",
                "dest display identity flipped instantly",
            )
            bks = os.listdir(m.AUTH_BACKUPS)
            check(len(bks) == 1, "displaced credentials backed up")
            evs = [json.loads(l) for l in open(m.AUTH_ROTATIONS)]
            check(
                evs[-1]["from_email"] == "b@x"
                and evs[-1]["to_email"] == "a@x"
                and (evs[-1]["reason"] == "test"),
                "rotation event logged with identities",
            )
            check(
                m.recent_auth_rotations(),
                "recent_auth_rotations surfaces it (dashboard)",
            )
            rc = 0
            try:
                m.cmd_rotate_auth([dst, "--from", src])
            except SystemExit as e:
                rc = e.code
            check(rc == 2, "rotating to the SAME account refuses (exit 2)")
            m.cmd_rotate_auth(["--restore", dst])
            check(
                json.load(open(os.path.join(dst, ".credentials.json")))[
                    "claudeAiOauth"
                ]["accessToken"]
                == "tokB",
                "restore returns the displaced tokens",
            )
            check(
                m._read_oauth_account(dst)["emailAddress"] == "b@x",
                "restore returns the identity",
            )
            csrc = os.path.join(st, "csrc")
            cdst = os.path.join(st, "cdst")
            os.makedirs(csrc)
            os.makedirs(cdst)
            open(os.path.join(csrc, "auth.json"), "w").write(
                '{"tokens":{"access_token":"CA"}}'
            )
            open(os.path.join(cdst, "auth.json"), "w").write(
                '{"tokens":{"access_token":"CB"}}'
            )
            m.cmd_rotate_auth([cdst, "--from", csrc, "--engine", "codex"])
            check(
                json.load(open(os.path.join(cdst, "auth.json")))["tokens"][
                    "access_token"
                ]
                == "CA",
                "codex rotation copies auth.json",
            )
            sw1 = os.path.join(st, "sw1")
            sw2 = os.path.join(st, "sw2")
            os.makedirs(sw1)
            os.makedirs(sw2)
            b1 = json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "S1",
                        "refreshToken": "r1",
                        "expiresAt": int(_t.time() + 9999) * 1000,
                    }
                }
            )
            b2 = json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "S2",
                        "refreshToken": "r2",
                        "expiresAt": int(_t.time() + 9999) * 1000,
                    }
                }
            )
            open(os.path.join(sw1, ".credentials.json"), "w").write(b1)
            open(os.path.join(sw2, ".credentials.json"), "w").write(b2)
            json.dump(
                {"oauthAccount": {"accountUuid": "U-1", "emailAddress": "one@x"}},
                open(os.path.join(sw1, ".claude.json"), "w"),
            )
            json.dump(
                {"oauthAccount": {"accountUuid": "U-2", "emailAddress": "two@x"}},
                open(os.path.join(sw2, ".claude.json"), "w"),
            )
            m.cmd_rotate_auth([sw1, "--from", sw2, "--swap"])
            check(
                json.load(open(os.path.join(sw1, ".credentials.json")))[
                    "claudeAiOauth"
                ]["accessToken"]
                == "S2"
                and json.load(open(os.path.join(sw2, ".credentials.json")))[
                    "claudeAiOauth"
                ]["accessToken"]
                == "S1",
                "claude --swap EXCHANGES both profiles' credentials (no duplicate account)",
            )
            check(
                m._read_oauth_account(sw1)["emailAddress"] == "two@x"
                and m._read_oauth_account(sw2)["emailAddress"] == "one@x",
                "claude --swap flips BOTH display identities",
            )
            cs1 = os.path.join(st, "cs1")
            cs2 = os.path.join(st, "cs2")
            os.makedirs(cs1)
            os.makedirs(cs2)
            open(os.path.join(cs1, "auth.json"), "w").write('{"t":"X1"}')
            open(os.path.join(cs2, "auth.json"), "w").write('{"t":"X2"}')
            m.cmd_rotate_auth([cs1, "--from", cs2, "--swap", "--engine", "codex"])
            check(
                json.load(open(os.path.join(cs1, "auth.json")))["t"] == "X2"
                and json.load(open(os.path.join(cs2, "auth.json")))["t"] == "X1",
                "codex --swap exchanges BOTH auth.json files",
            )
    finally:
        (
            m.STATE_DIR,
            m.AUTH_BACKUPS,
            m.AUTH_ROTATIONS,
            m.USAGE_DIR,
            m.write_claude_cred_blob,
            m.read_claude_cred_blob,
            _env_prof,
        ) = saved
        if _env_prof is None:
            os.environ.pop("NEOMAX_PROFILES", None)
        else:
            os.environ["NEOMAX_PROFILES"] = _env_prof
