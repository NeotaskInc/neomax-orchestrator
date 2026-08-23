"""Mechanical integration and conflict resolution."""


def union_resolve(text):
    """Resolve git conflict markers by UNIONing both sides (preserve order, dedup).
    Safe ONLY for append-only/line-set files. Returns None if it can't safely
    resolve — including diff3/zdiff3 conflicts (with a '|||||||' base section),
    which this 2-way union would corrupt."""
    if "\n|||||||" in "\n" + text or text.startswith("|||||||"):
        return None
    lines = text.split("\n")
    (out, i) = ([], 0)
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            (ours, theirs) = ([], [])
            i += 1
            while (
                i < len(lines)
                and (not lines[i].startswith("======="))
                and (not lines[i].startswith("|||||||"))
            ):
                ours.append(lines[i])
                i += 1
            if i >= len(lines) or not lines[i].startswith("======="):
                return None
            i += 1
            while i < len(lines) and (not lines[i].startswith(">>>>>>>")):
                theirs.append(lines[i])
                i += 1
            if i >= len(lines):
                return None
            i += 1
            (seen, merged) = (set(ours), list(ours))
            for line in theirs:
                if line not in seen:
                    merged.append(line)
                    seen.add(line)
            out.extend(merged)
        else:
            out.append(lines[i])
            i += 1
    joined = "\n".join(out)
    return None if "<<<<<<<" in joined or ">>>>>>>" in joined else joined


def try_mechanical_resolve(integ_wt):
    """After a failed merge, union-resolve ONLY whitelisted conflicts. Returns the
    list of conflicts it could NOT safely resolve (empty = all resolved)."""
    from . import area_locks as _area_locks
    from . import config as _config
    from . import gitops as _gitops

    conflicted = _gitops.git(
        ["diff", "--name-only", "--diff-filter=U"], integ_wt
    ).stdout.split()
    remaining = []
    for f in conflicted:
        if not _area_locks.UNION_SAFE_RE.search(f):
            remaining.append(f)
            continue
        path = _config.os.path.join(integ_wt, f)
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            remaining.append(f)
            continue
        if "�" in raw:
            remaining.append(f)
            continue
        merged = union_resolve(raw)
        if merged is None:
            remaining.append(f)
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(merged)
        _gitops.git(["add", f], integ_wt)
    return remaining


def integrate_part(repo, integ_wt, integ_branch, part_branch, part_id):
    """Merge a finished part's branch into the integration branch (in a dedicated
    integration worktree, never the user's checkout). Two-tier conflict-self-heal:
    a clean merge, else union-resolve whitelisted (append-only) conflicts and
    complete; real/code conflicts → abort + return False (left for delegate/manual).
    Returns True if merged/empty, False on unresolved conflict."""
    from . import gitops as _gitops
    from . import models as _models

    ahead = (
        _gitops.git(
            ["rev-list", "--count", "%s..%s" % (integ_branch, part_branch)], repo
        ).stdout.strip()
        or "0"
    )
    if ahead == "0":
        return True
    m = _gitops.git(
        [
            "merge",
            "--no-ff",
            "-m",
            "integrate %s (%s)" % (part_id, part_branch),
            part_branch,
        ],
        integ_wt,
    )
    if m.returncode == 0:
        return True
    remaining = try_mechanical_resolve(integ_wt)
    if not remaining:
        if _gitops.git(["commit", "--no-edit"], integ_wt).returncode == 0 and (
            not _gitops.git(
                ["diff", "--name-only", "--diff-filter=U"], integ_wt
            ).stdout.strip()
        ):
            _models.err("    (self-healed append-only conflicts for %s)" % part_id)
            return True
    _gitops.git(["merge", "--abort"], integ_wt)
    return False
