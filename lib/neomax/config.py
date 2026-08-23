"""Runtime paths, pricing, and provider-scope configuration."""

import base64
import datetime
import email.utils
import fcntl
import glob as globmod
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid as uuidlib

HOME = os.path.expanduser("~")
NEOMAX_REPO_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
STATE_DIR = os.environ.get("NEOMAX_HOME") or os.path.join(HOME, ".neomax")
RUNS_DIR = os.path.join(STATE_DIR, "runs")
LOGS_DIR = os.path.join(STATE_DIR, "logs")
USAGE_DIR = os.path.join(STATE_DIR, "usage")
EVENTS_DIR = os.path.join(STATE_DIR, "events")
WORKTREES_DIR = os.path.join(STATE_DIR, "worktrees")
COOLDOWN_FILE = os.path.join(STATE_DIR, "cooldown.json")
PAUSED_FILE = os.path.join(STATE_DIR, "paused.json")
HISTORY_DB = os.path.join(STATE_DIR, "history.db")
HISTORY_LOGS = os.path.join(STATE_DIR, "history-logs")
USAGE_LEDGER_DIR = os.path.join(STATE_DIR, "usage-ledger")
USAGE_WATCH_STATE = os.path.join(STATE_DIR, "usage-watch.state.json")
CLAUDE_BIN = os.environ.get("NEOMAX_CLAUDE_BIN", "claude")
CODEX_BIN = os.environ.get("NEOMAX_CODEX_BIN", "codex")
OPENCODE_BIN = os.environ.get("NEOMAX_OPENCODE_BIN", "opencode")
KIMI_BIN = os.environ.get("NEOMAX_KIMI_BIN", "kimi")
GROK_BIN = os.environ.get("NEOMAX_GROK_BIN", "grok")
CACHE_READ_MULT = 0.1
CLAUDE_CACHE_WRITE_MULT = 1.25
OPENAI_CACHE_WRITE_MULT = 0.0
MODEL_IO = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (0.5, 4.0),
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4": (2.5, 15.0),
    "kimi-code/k3": (0.0, 0.0),
    "kimi-code/kimi-for-coding": (0.0, 0.0),
    "grok-4.6": (0.0, 0.0),
}


def _derive_pricing(model, base_in, base_out):
    """Full rate dict for a model = base in/out + cache rates DERIVED from the documented
    multipliers (never hand-typed). OpenAI models (gpt*/o*) bill no cache write."""
    openai = model.startswith("gpt") or model.startswith("o")
    cw_mult = OPENAI_CACHE_WRITE_MULT if openai else CLAUDE_CACHE_WRITE_MULT
    return {
        "in": base_in,
        "out": base_out,
        "cw": round(base_in * cw_mult, 4),
        "cr": round(base_in * CACHE_READ_MULT, 4),
    }


PRICING = {m: _derive_pricing(m, i, o) for (m, (i, o)) in MODEL_IO.items()}
PRICING_DEFAULT = _derive_pricing("claude-fable-5", 10.0, 50.0)


def model_pricing(model):
    m = (model or "").lower().replace("[1m]", "").strip()
    if m in PRICING:
        return PRICING[m]
    for k, v in PRICING.items():
        if m.startswith(k):
            return v
    return PRICING_DEFAULT


def usage_cost(model, n_in, n_out, n_cw, n_cr):
    """Estimated USD for one completion's tokens at the model's API rates. Tokens passed
    as RAW counts; rates are per-million. `n_in` is NON-cached input (cache write/read
    are separate). Returns a float (dollars)."""
    p = model_pricing(model)
    return (
        n_in * p["in"] + n_out * p["out"] + n_cw * p["cw"] + n_cr * p["cr"]
    ) / 1000000.0


DEFAULT_COOLDOWN_S = 30 * 60


def allowed_engines():
    """Which engines' accounts the orchestrator may use as the worker pool, from the
    NEOMAX_FLEET env the launcher sets: one engine, `all`, or any comma/plus-separated
    engine subset. A worker on an out-of-scope engine is refused."""
    v = (os.environ.get("NEOMAX_FLEET") or "all").strip().lower()
    universe = {"claude", "codex", "opencode", "kimi", "grok"}
    aliases = {
        "claude": {"claude"},
        "claude-only": {"claude"},
        "codex": {"codex"},
        "codex-only": {"codex"},
        "opencode": {"opencode"},
        "opencode-only": {"opencode"},
        "ox": {"opencode"},
        "ox-only": {"opencode"},
        "kimi": {"kimi"},
        "kimi-only": {"kimi"},
        "kimi-code": {"kimi"},
        "kimi-code-only": {"kimi"},
        "grok": {"grok"},
        "grok-only": {"grok"},
        "xai": {"grok"},
        "xai-only": {"grok"},
    }
    if v in aliases:
        return aliases[v]
    if v == "all":
        return universe
    parts = {aliases.get(x, {x}).copy().pop() for x in re.split("[,+]", v) if x}
    return parts if parts and parts <= universe else universe


def engine_in_scope(engine):
    return engine in allowed_engines()


CLAUDE_OPUS5_MODEL = "claude-opus-5"
CLAUDE_OPUS_MODEL = CLAUDE_OPUS5_MODEL
CLAUDE_OPUS_MODEL_1M = CLAUDE_OPUS_MODEL + "[1m]"
CLAUDE_FABLE_MODEL = "claude-fable-5"
CLAUDE_OPUS48_MODEL = "claude-opus-4-8"
CLAUDE_DEFAULT_MODEL = os.environ.get("NEOMAX_DEFAULT_MODEL", CLAUDE_FABLE_MODEL)
CLAUDE_DEFAULT_MODEL_1M = CLAUDE_DEFAULT_MODEL + "[1m]"
CODEX_MODELS = {"sol": "gpt-5.6-sol", "terra": "gpt-5.6-terra", "luna": "gpt-5.6-luna"}
CODEX_MODEL = CODEX_MODELS["sol"]
CODEX_MODEL_DEFAULT_TIER = "sol"
