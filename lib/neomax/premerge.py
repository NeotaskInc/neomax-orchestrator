"""Premerge coordination and command usage."""


def cmd_premerge_check(argv):
    """Pre-merge-to-main coordination: fetch the repo and report whether main moved (another
    orchestrator pushed) + which OTHER live orchestrators own work on this repo. Run it before
    merging/pushing so concurrent orchestrators don't clobber each other.
       neomax premerge-check [REPO_PATH] [--base main] [--json]"""
    from . import config as _config
    from . import gitops as _gitops
    from . import orchestrators as _orchestrators

    base = "main"
    repo = _config.os.getcwd()
    for i, a in enumerate(argv):
        if a == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
        elif not a.startswith("-"):
            repo = _config.os.path.abspath(a)
    out = {
        "repo": repo,
        "base": base,
        "main_moved": False,
        "fetched": False,
        "behind": 0,
        "other_orchestrators": [],
    }
    try:
        top = _gitops.git(["rev-parse", "--show-toplevel"], repo)
        if top.returncode == 0:
            repo = top.stdout.strip()
            out["repo"] = repo
        f = _gitops.git(["fetch", "origin", base], repo)
        out["fetched"] = f.returncode == 0
        rc = _gitops.git(["rev-list", "--count", "%s..origin/%s" % (base, base)], repo)
        if rc.returncode == 0 and rc.stdout.strip().isdigit():
            out["behind"] = int(rc.stdout.strip())
            out["main_moved"] = out["behind"] > 0
    except Exception:
        pass
    me = _orchestrators.current_orch_session()
    for o in _orchestrators.live_orchestrators():
        if o.get("session") == me:
            continue
        ocwd = o.get("cwd") or ""
        if ocwd and (ocwd.startswith(repo) or repo.startswith(ocwd)):
            out["other_orchestrators"].append(
                {
                    "engine": o.get("engine"),
                    "account": o.get("account"),
                    "project": o.get("project"),
                    "branch_prefix": o.get("branch_prefix"),
                }
            )
    if "--json" in argv:
        print(_config.json.dumps(out))
        return
    if out["main_moved"]:
        print(
            "⚠ origin/%s moved — you are %d commit(s) behind. PULL/rebase your integration onto it BEFORE merging, or you'll create a divergent main."
            % (base, out["behind"])
        )
    else:
        print(
            "origin/%s has not moved since your base%s."
            % (base, "" if out["fetched"] else " (fetch failed — check manually)")
        )
    if out["other_orchestrators"]:
        print(
            "⚠ %d other live orchestrator(s) are working on this repo — coordinate the merge so neither overwrites the other:"
            % len(out["other_orchestrators"])
        )
        for o in out["other_orchestrators"]:
            print(
                "    %s acct %s · project %s · branch `%s/`"
                % (o["engine"], o["account"], o["project"], o.get("branch_prefix"))
            )
    else:
        print("no other live orchestrator is working on this repo.")


def usage():
    from . import config as _config
    from . import models as _models

    _models.err(
        'usage: neomax delegate [-u|--opus] [--engine claude|codex|opencode|kimi|grok] [--model MODEL] [-e EFFORT] [-t MIN] [-s MIN] [-n] [--pr] [--base REF] [--no-worktree] auto|N "prompt"'
    )
    _models.err(
        "       neomax run-all PLAN.json   (fan-out scheduler: many parts across accounts)"
    )
    _models.err(
        "       neomax ls [--hook] | log RUNID | audit [RUNID] | resume RUNID [PROMPT] | retry RUNID [auto|N] | kill RUNID | pr RUNID|--branch NAME | reconcile [--heal [--max N] [--max-age-hours H] [--allow-repeat]] | ack RUNID|--all | clean [--force] RUNID|--done"
    )
    _config.sys.exit(2)
