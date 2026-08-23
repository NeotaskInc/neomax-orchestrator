"""Shared hermetic fixtures and compatibility state for Neomax tests."""

import contextlib, importlib, importlib.util, io, json, os, re, subprocess, sys, tempfile, time
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
NEOMAX = os.path.join(ROOT, "lib", "neomax_core.py")
LIB = os.path.join(ROOT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)
import neomax as m

_real_weekly_reset_at = m.weekly_reset_at
m.weekly_reset_at = lambda profile, engine=None: None
PASS = 0


def check(cond, label):
    global PASS
    if not cond:
        print("FAIL:", label)
        sys.exit(1)
    PASS += 1
    print("  ok:", label)


def _env(
    state, claude_profiles, codex_profiles, opencode_profiles=None, kimi_profiles=None
):
    e = dict(os.environ)
    e["NEOMAX_HOME"] = state
    e["NEOMAX_PROFILES"] = claude_profiles
    e["NEOMAX_CODEX_PROFILES"] = codex_profiles
    e["NEOMAX_OPENCODE_PROFILES"] = opencode_profiles or state + "/p3"
    e["NEOMAX_KIMI_PROFILES"] = kimi_profiles or state + "/p4"
    return e


def _mk_repo(tmp, branch="neomax/t1"):
    """A real git repo + a worktree one commit ahead of main (the standard 'has
    unmerged work' fixture). Returns (repo, worktree, g) where g runs git in repo."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)

    def g(*args, cwd=repo):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false"] + list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    g("init")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    with open(os.path.join(repo, "f.txt"), "w") as f:
        f.write("base\n")
    g("add", ".")
    g("commit", "-m", "base")
    g("branch", "-M", "main")
    wt = os.path.join(tmp, "wt")
    r = g("worktree", "add", wt, "-b", branch, "main")
    assert r.returncode == 0, r.stderr
    with open(os.path.join(wt, "new.txt"), "w") as f:
        f.write("work\n")
    g("add", ".", cwd=wt)
    r = g("commit", "-m", "work", cwd=wt)
    assert r.returncode == 0, r.stderr
    return (repo, wt, g)


def every(fn, items):
    return all((fn(i) for i in items))


STUB_GH = '#!/bin/bash\n# minimal `gh` stub for the issue-pipeline test\nif [ "$1 $2" = "issue create" ]; then echo "https://github.com/o/repo/issues/$$"; exit 0; fi\nif [ "$1 $2" = "issue view" ]; then echo \'{"state":"OPEN"}\'; exit 0; fi\nexit 0\n'
__all__ = tuple((name for name in globals() if not name.startswith("__")))
