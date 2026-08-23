#!/usr/bin/env python3
"""Hermetic tests for standalone-rotate/crotate — no real accounts, no keychain,
no network. Profiles are stubbed via CROTATE_PROFILES / CROTATE_CODEX_PROFILES,
state via CROTATE_HOME, keychain via CROTATE_NO_KEYCHAIN=1, HTTP by monkeypatching
the module seams. Run: python3 standalone-rotate/test_crotate.py"""

import base64
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CROTATE = os.path.join(HERE, "crotate")

TMP = tempfile.mkdtemp(prefix="crotate-test-")
STATE = os.path.join(TMP, "state")
PROF_A = os.path.join(TMP, "claude-a")
PROF_B = os.path.join(TMP, "claude-b")
CX_A = os.path.join(TMP, "codex-a")
CX_B = os.path.join(TMP, "codex-b")

os.environ["CROTATE_HOME"] = STATE
os.environ["CROTATE_PROFILES"] = "%s:%s" % (PROF_A, PROF_B)
os.environ["CROTATE_CODEX_PROFILES"] = "%s:%s" % (CX_A, CX_B)
os.environ["CROTATE_NO_KEYCHAIN"] = "1"

spec = importlib.util.spec_from_loader(
    "crotate", SourceFileLoader("crotate", CROTATE))
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

CHECKS = 0


def ok(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        print("FAIL: %s" % label)
        sys.exit(1)
    print("  ok  %s" % label)


def seed_claude(profile, uuid, email, token):
    os.makedirs(profile, exist_ok=True)
    blob = {"claudeAiOauth": {"accessToken": token, "refreshToken": "r-" + token,
                              "expiresAt": int((time.time() + 3600) * 1000)}}
    with open(os.path.join(profile, ".credentials.json"), "w") as f:
        json.dump(blob, f)
    with open(os.path.join(profile, ".claude.json"), "w") as f:
        json.dump({"oauthAccount": {"accountUuid": uuid, "emailAddress": email,
                                    "displayName": email.split("@")[0]}}, f)


def seed_codex(profile, account_id, email):
    os.makedirs(profile, exist_ok=True)
    payload = {"email": email,
               "https://api.openai.com/auth": {"chatgpt_account_id": account_id,
                                               "chatgpt_plan_type": "pro"}}
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with open(os.path.join(profile, "auth.json"), "w") as f:
        json.dump({"tokens": {"access_token": "at-" + account_id,
                              "id_token": "h.%s.s" % seg}}, f)


def cred_token(profile):
    with open(os.path.join(profile, ".credentials.json")) as f:
        return json.load(f)["claudeAiOauth"]["accessToken"]


def patch_usage(m):
    """fetch_claude_usage → fixed per-profile-basename readings, no network."""
    def fake(profile):
        d = m.get(os.path.basename(profile))
        if d is None:
            return None
        return {"five_hour": {"used_percent": d[0], "resets_at": time.time() + 1800},
                "seven_day": {"used_percent": d[1], "resets_at": time.time() + 86400},
                "source": "claude-api", "observed_at": int(time.time())}
    cr.fetch_claude_usage = fake


print("== utils ==")
ok(cr.to_epoch_seconds(1.7e12) and cr.to_epoch_seconds(1.7e12) < 1e11,
   "to_epoch_seconds scales ms to s")
ok(cr.to_epoch_seconds("nope") is None, "to_epoch_seconds tolerates garbage")
ok(cr.normalize_reset_epoch(time.time() - 10) is None, "normalize rejects past resets")
ok(cr.normalize_reset_epoch(time.time() + 1e9) is None, "normalize rejects absurd resets")

print("== profiles ==")
ok(cr.engine_profiles("claude") == [PROF_A, PROF_B], "claude profiles from env")
ok(cr.engine_profiles("codex") == [CX_A, CX_B], "codex profiles from env")
ok(cr._resolve_profile("2", "claude") == PROF_B, "selector 2 -> second profile")
ok(cr._parse_account_selectors(["account", "two", "acct3"]) == ["2", "3"],
   "loose selector parsing")

print("== cooldowns / pause ==")
cr.set_cooldown(PROF_A, time.time() + 600)
ok(cr.cooling_down(PROF_A) > 0, "cooldown set + read")
cr.clear_cooldown(PROF_A)
ok(cr.cooling_down(PROF_A) == 0, "cooldown cleared")
cr.set_cooldown(PROF_A, "garbage")
ok(0 < cr.cooling_down(PROF_A) <= time.time() + cr.DEFAULT_COOLDOWN_S + 5,
   "malformed cooldown degrades to default, not crash")
cr.clear_cooldown(PROF_A)
cr.set_paused(PROF_A, True)
ok(cr.is_paused(PROF_A), "pause")
cr.set_paused(PROF_A, False)
ok(not cr.is_paused(PROF_A), "unpause")

print("== armed markers ==")
cr.set_armed_rotate(PROF_A, 95.0, ["2"], engine="claude")
got = cr.armed_rotate_take(PROF_A, "sess-1")
ok(got and got["threshold"] == 95.0 and got["prefer"] == ["2"], "arm + take claims")
ok(cr.armed_rotate_take(PROF_A, "sess-2") is None,
   "a different session cannot take a claimed marker")
ok(cr.armed_rotate_take(PROF_A, "sess-1") is not None, "owner re-takes fine")
cr.clear_armed_rotate(PROF_A)
ok(cr.armed_rotate_take(PROF_A, "sess-1") is None, "disarm clears the marker")

print("== claude session_rotate (swap) ==")
seed_claude(PROF_A, "uuid-a", "a@x.com", "tok-a")
seed_claude(PROF_B, "uuid-b", "b@x.com", "tok-b")
patch_usage({os.path.basename(PROF_A): (98.0, 40.0),
             os.path.basename(PROF_B): (10.0, 20.0)})
msg = cr.session_rotate(profile=PROF_A, threshold=97.0, force=True, engine="claude")
ok(msg.startswith("ROTATED"), "over-threshold rotate happens: %s" % msg[:60])
ok(cred_token(PROF_A) == "tok-b" and cred_token(PROF_B) == "tok-a",
   "credentials actually SWAPPED (no duplicate)")
ok(cr._read_oauth_account(PROF_A).get("accountUuid") == "uuid-b",
   "oauthAccount identity swapped too")
ok(cr.cooling_down(PROF_B) > 0, "spent account's new slot cooled until reset")
ok(cr._account_cooled("uuid-a"), "spent account cooled by uuid as well")
ok(any(r.get("from_email") == "a@x.com" for r in cr.recent_auth_rotations()),
   "rotation logged")

# not-over + force + already freshest → stays put
patch_usage({os.path.basename(PROF_A): (5.0, 10.0),
             os.path.basename(PROF_B): (50.0, 20.0)})
cr.clear_cooldown(PROF_B)
d = cr._account_cooldowns()
d.pop("uuid-a", None)
cr._atomic_json(cr.ACCOUNT_COOLDOWN_FILE, d)
msg = cr.session_rotate(profile=PROF_A, threshold=97.0, force=True, engine="claude")
ok("already on the most available" in msg, "fresh account + no better target stays put")

# dry-run never swaps
patch_usage({os.path.basename(PROF_A): (98.0, 40.0),
             os.path.basename(PROF_B): (10.0, 20.0)})
before = cred_token(PROF_A)
msg = cr.session_rotate(profile=PROF_A, threshold=97.0, force=True, dry_run=True,
                        engine="claude")
ok(msg.startswith("DRY-RUN") and cred_token(PROF_A) == before, "dry-run is a no-op")

# unusable named target is reported honestly
msg = cr.session_rotate(profile=PROF_A, prefer=["9"], force=True, engine="claude")
ok("can't be rotated to" in msg or "no rotation" in msg, "unusable named target refused")

print("== codex session_rotate (auth.json swap) ==")
seed_codex(CX_A, "acct-a", "ca@x.com")
seed_codex(CX_B, "acct-b", "cb@x.com")


def fake_codex(profile):
    m = {os.path.basename(CX_A): (99.0, 50.0), os.path.basename(CX_B): (12.0, 30.0)}
    d = m.get(os.path.basename(profile))
    return {"five_hour": {"used_percent": d[0], "resets_at": time.time() + 1200},
            "seven_day": {"used_percent": d[1], "resets_at": time.time() + 86400},
            "source": "codex-rollout", "observed_at": int(time.time())}


cr.fetch_codex_usage = fake_codex
ok(cr.codex_identity(CX_A)["key"] == "acct-a", "codex identity from JWT")
msg = cr.session_rotate(profile=CX_A, threshold=97.0, force=True, engine="codex")
ok(msg.startswith("ROTATED"), "codex rotate happens: %s" % msg[:60])
ok(cr.codex_identity(CX_A)["key"] == "acct-b" and cr.codex_identity(CX_B)["key"] == "acct-a",
   "codex auth.json swapped both ways")
ok(cr.cooling_down(CX_B) > 0, "spent codex slot cooled")

print("== tick (safety net) ==")
seed_claude(PROF_A, "uuid-a2", "a2@x.com", "tok-a2")
seed_claude(PROF_B, "uuid-b2", "b2@x.com", "tok-b2")
patch_usage({os.path.basename(PROF_A): (99.0, 40.0),
             os.path.basename(PROF_B): (5.0, 20.0)})
cr._atomic_json(cr.COOLDOWN_FILE, {})
cr._atomic_json(cr.ACCOUNT_COOLDOWN_FILE, {})
cr.set_armed_rotate(PROF_A, 97.0, [], engine="claude")
n = cr.rotation_tick()
ok(n == 1 and cred_token(PROF_A) == "tok-b2", "tick rotates an armed walled profile")
cr.clear_armed_rotate(PROF_A)

print("== status ==")
st = cr.gather_status()
ok(set(st["engines"].keys()) == {"claude", "codex"}, "status covers both engines")
ok(len(st["engines"]["claude"]) == 2 and st["engines"]["claude"][0]["acct_no"] == 1,
   "status lists accounts with numbers")
ok(isinstance(st["rotations"], list) and st["rotations"], "status carries rotation log")
json.dumps(st)  # must be serializable (the portal serves it)
ok(True, "status is JSON-serializable")

print("== live sessions + sub-agents (ambient, both engines) ==")
SESS = "11111111-2222-3333-4444-555555555555"
IDLE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
proj = os.path.join(PROF_A, "projects", "-tmp-proj")
os.makedirs(os.path.join(proj, SESS, "subagents"), exist_ok=True)
with open(os.path.join(proj, SESS + ".jsonl"), "w") as f:  # mid-turn main window
    f.write(json.dumps({"type": "user", "sessionId": SESS, "cwd": "/tmp/proj",
                        "gitBranch": "main", "slug": "tmp-proj",
                        "message": {"content": "build the thing"}}) + "\n")
with open(os.path.join(proj, IDLE + ".jsonl"), "w") as f:  # open but idle window
    f.write(json.dumps({"type": "user", "sessionId": IDLE, "cwd": "/tmp/proj",
                        "message": {"content": "earlier task"}}) + "\n")
    f.write(json.dumps({"type": "assistant",
                        "message": {"stop_reason": "end_turn"}}) + "\n")
with open(os.path.join(proj, SESS, "subagents", "agent-1.jsonl"), "w") as f:  # working
    f.write(json.dumps({"sessionId": SESS, "cwd": "/tmp/proj",
                        "message": {"content": [{"type": "text",
                                                 "text": "scout the repo"}]}}) + "\n")
    f.write(json.dumps({"type": "assistant",
                        "message": {"usage": {"input_tokens": 100, "output_tokens": 50,
                                              "cache_read_input_tokens": 10},
                                    "stop_reason": None}}) + "\n")
with open(os.path.join(proj, SESS, "subagents", "agent-2.jsonl"), "w") as f:  # finished
    f.write(json.dumps({"sessionId": SESS, "cwd": "/tmp/proj",
                        "message": {"content": [{"type": "text", "text": "done one"}]}}) + "\n")
    f.write(json.dumps({"type": "assistant",
                        "message": {"stop_reason": "end_turn"}}) + "\n")
CXS = "0199aaaa-bbbb-cccc-dddd-eeeeffff0000"
cxdir = os.path.join(CX_A, "sessions", "2026", "07", "08")
os.makedirs(cxdir, exist_ok=True)
with open(os.path.join(cxdir, "rollout-2026-07-08T01-00-00-%s.jsonl" % CXS), "w") as f:
    f.write(json.dumps({"payload": {"type": "session_meta", "cwd": "/tmp/cxproj"}}) + "\n")
    f.write(json.dumps({"payload": {"type": "user_message",
                                    "message": "port the module"}}) + "\n")
    f.write(json.dumps({"payload": {"type": "task_started"}}) + "\n")
with open(os.path.join(cxdir, "rollout-2026-07-08T00-00-00-deadbeef-0000-0000-0000-000000000000.jsonl"), "w") as f:
    f.write(json.dumps({"payload": {"type": "session_meta", "cwd": "/tmp/cxgone"}}) + "\n")
    f.write(json.dumps({"payload": {"type": "shutdown_complete"}}) + "\n")
st = cr.gather_status()
by_id = {s["session"]: s for s in st["sessions"]}
ok(SESS in by_id and by_id[SESS]["working"] and by_id[SESS]["engine"] == "claude",
   "mid-turn claude window surfaces as a working session")
ok(by_id[SESS]["label"] == "build the thing" and by_id[SESS]["cwd"] == "/tmp/proj",
   "session carries label + cwd from its transcript head")
ok(IDLE in by_id and not by_id[IDLE]["working"],
   "idle-but-open claude window still listed (marked idle)")
ags = by_id[SESS]["agents"]
ok(len(ags) == 1 and ags[0]["working"] and ags[0]["label"] == "scout the repo",
   "working sub-agent attaches to its session; finished standalone agent skipped")
ok(ags[0]["tok"] == {"in": 100, "out": 50, "cache": 10},
   "sub-agent tokens summed from its transcript")
ok(CXS in by_id and by_id[CXS]["working"] and by_id[CXS]["cwd"] == "/tmp/cxproj",
   "live codex rollout surfaces as a working session")
ok("deadbeef-0000-0000-0000-000000000000" not in by_id,
   "closed codex session (shutdown_complete) excluded")
acct_a = st["engines"]["claude"][0]
ok(acct_a["mains"] == 2 and acct_a["subagents"] == 1,
   "account carries mains + working-sub-agent counts")
json.dumps(st)
ok(True, "status with sessions is JSON-serializable")

print("== portal (subprocess, localhost only) ==")
import socket
import urllib.request
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
env = dict(os.environ)
env["CROTATE_NO_OPEN"] = "1"  # never pop a browser from the test suite
proc = subprocess.Popen([sys.executable, CROTATE, "portal", "--port", str(port)],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    body = None
    for _ in range(50):
        try:
            body = urllib.request.urlopen(
                "http://127.0.0.1:%d/status.json" % port, timeout=2).read()
            break
        except OSError:
            time.sleep(0.1)
    ok(body is not None, "portal serves within 5s")
    ok(set(json.loads(body)["engines"].keys()) == {"claude", "codex"},
       "portal /status.json returns gather_status")
    html = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2).read()
    ok(b"<!doctype html" in html.lower(), "portal / serves the dashboard html")
    # the portal must keep the neomax-portal look (same CSS/markup, ported verbatim)
    for marker in (b"--accent:#ff5c00", b'data-theme="light"', b"themeBtn",
                   b"claudeAccts", b"codexAccts", b"id=stats", b"id=authrot",
                   b"class=usline", b"chip", b"id=active", b"id=runningCount",
                   b"Tasks in progress", b"Interactive sessions"):
        ok(marker in html, "portal html keeps neomax-portal UI marker %r" % marker)
finally:
    proc.terminate()
    proc.wait(timeout=10)

print("== usage-hook (subprocess, garbage stdin) ==")
env = dict(os.environ)
env["CLAUDE_CONFIG_DIR"] = PROF_A
r = subprocess.run([sys.executable, CROTATE, "usage-hook"], input=b"not json",
                   env=env, capture_output=True, timeout=30)
ok(r.returncode == 0, "usage-hook always exits 0 (never blocks Claude)")

print("== run / claudeN helpers ==")
ok(cr._parse_run_args(["3", "-r"]) == ("claude", 3, ["-r"]),
   "run: engine args after N pass through")
ok(cr._parse_run_args(["--engine", "codex", "2"]) == ("codex", 2, []),
   "run: --engine before N")
ok(cr._parse_run_args([])[1] is None and cr._parse_run_args(["-x"])[1] is None,
   "run: missing/invalid account -> None")
prof, env = cr._session_env(1, "claude")
ok(prof == PROF_A and "CLAUDE_CONFIG_DIR" not in env,
   "acct 1 runs with CLAUDE_CONFIG_DIR unset")
prof, env = cr._session_env(2, "claude")
ok(env.get("CLAUDE_CONFIG_DIR") == PROF_B and "ANTHROPIC_API_KEY" not in env,
   "acct N sets CLAUDE_CONFIG_DIR and scrubs API-key vars")
prof, env = cr._session_env(2, "codex")
ok(env.get("CODEX_HOME") == CX_B, "codex acct N sets CODEX_HOME")

# run refuses an account that was never added (not authenticated) — exit 1 + message
fake_home = os.path.join(TMP, "fake-home")
os.makedirs(fake_home, exist_ok=True)
no_auth = os.path.join(TMP, "claude-noauth")
os.makedirs(no_auth, exist_ok=True)
renv = dict(os.environ)
renv["HOME"] = fake_home
renv["CROTATE_PROFILES"] = "%s:%s" % (PROF_A, no_auth)
r = subprocess.run([sys.executable, CROTATE, "run", "2"], env=renv,
                   capture_output=True, text=True, timeout=30)
ok(r.returncode == 1 and "no claude account 2 added" in r.stderr,
   "run fails cleanly for an unauthenticated account")
r = subprocess.run([sys.executable, CROTATE, "run", "10"], env=renv,
                   capture_output=True, text=True, timeout=30)
ok(r.returncode == 1 and "no claude account 10 added" in r.stderr,
   "run fails cleanly for a never-created account slot")
r = subprocess.run([sys.executable, CROTATE, "run", "--engine", "codex", "7"], env=renv,
                   capture_output=True, text=True, timeout=30)
ok(r.returncode == 1 and "no codex account 7 added" in r.stderr
   and "--engine codex" in r.stderr, "codex run failure names the codex login command")
# an AUTHENTICATED account execs the engine (fake claude binary on PATH)
bindir = os.path.join(TMP, "fakebin")
os.makedirs(bindir, exist_ok=True)
with open(os.path.join(bindir, "claude"), "w") as f:
    f.write("#!/bin/sh\necho FAKE-CLAUDE cfg=${CLAUDE_CONFIG_DIR:-UNSET}\n")
os.chmod(os.path.join(bindir, "claude"), 0o755)
renv["PATH"] = bindir + os.pathsep + renv.get("PATH", "")
r = subprocess.run([sys.executable, CROTATE, "run", "1"], env=renv,
                   capture_output=True, text=True, timeout=30)
ok(r.returncode == 0 and "FAKE-CLAUDE cfg=UNSET" in r.stdout,
   "run execs the engine for an authenticated account (acct 1 env unset)")

print("== malformed state never crashes ==")
with open(cr.ARMED_ROTATE_FILE, "w") as f:
    f.write("{broken json")
ok(cr._armed_rotate_map() == {}, "broken armed file degrades to empty")
with open(cr.COOLDOWN_FILE, "w") as f:
    f.write("[not, a, map")
ok(cr.load_cooldowns() == {}, "broken cooldown file degrades to empty")
ok(cr.rotation_tick() == 0, "tick survives broken state files")
st = cr.gather_status()
ok(set(st["engines"].keys()) == {"claude", "codex"}, "status survives broken state files")

shutil.rmtree(TMP, ignore_errors=True)
print("\nALL OK — %d checks passed" % CHECKS)
