"""GitHub CLI access and project repository paths."""

from . import config as _config

ISSUES_DIR = _config.os.path.join(_config.STATE_DIR, "issues")
ISSUE_LABEL = _config.os.environ.get("NEOMAX_ISSUE_LABEL", "neomax-issue")
GH_BIN = _config.os.environ.get("NEOMAX_GH_BIN", "gh")
ISSUE_STATUSES = ("open", "claimed", "fixing", "pr", "done", "closed")
ISSUE_CLAIM_TTL_S = float(
    _config.os.environ.get("NEOMAX_ISSUE_CLAIM_TTL", str(6 * 3600))
)


def gh_available():
    from . import config as _config

    return bool(_config.shutil.which(GH_BIN))


def gh_run(args, cwd, timeout=90):
    """Run `gh ...` in cwd; return CompletedProcess, or a synthetic failure (never raises)."""
    from . import config as _config

    try:
        return _config.subprocess.run(
            [GH_BIN] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except (
        FileNotFoundError,
        NotADirectoryError,
        _config.subprocess.TimeoutExpired,
        OSError,
    ):
        return _config.subprocess.CompletedProcess(
            [GH_BIN] + args, 1, "", "gh unavailable"
        )


def gh_json(args, cwd, timeout=90):
    from . import config as _config

    r = gh_run(args, cwd, timeout)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        return _config.json.loads(r.stdout)
    except ValueError:
        return None


def project_repo_paths(project=None):
    """Absolute (repo_name, path) for a project's repos, from the REGISTRY (no hardcoding). A repo
    entry '.' is the project root itself. Only repos that exist on disk are returned. Defaults to
    the cwd's project."""
    from . import config as _config
    from . import projects as _projects

    project = project or _projects.project_of(_config.os.getcwd())
    cfg = _projects.load_projects().get(project) or {} if project else {}
    root = _config.os.path.abspath(_config.os.path.expanduser(cfg.get("root", "")))
    out = []
    for name in cfg.get("repos") or []:
        path = root if name == "." else _config.os.path.join(root, name)
        if _config.os.path.isdir(path):
            out.append((_config.os.path.basename(root) if name == "." else name, path))
    return out
