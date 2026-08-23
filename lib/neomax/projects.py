"""Portable project discovery and registration."""

from . import config as _config

DEFAULT_PROJECTS_FILE = _config.os.path.join(_config.STATE_DIR, "projects.json")
PROJECTS_FILE = DEFAULT_PROJECTS_FILE
LOCAL_PROJECTS_FILE = _config.os.environ.get(
    "NEOMAX_PROJECTS_CONFIG",
    _config.os.path.join(_config.NEOMAX_REPO_DIR, "project", "projects.local.json"),
)
_DEFAULT_PROJECTS = {}


def _project_map(path):
    from . import config as _config

    try:
        with open(path) as f:
            data = _config.json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def load_projects():
    """Return local seed projects plus the per-user registry."""
    from . import config as _config

    projects = {}
    explicit_local = _config.os.environ.get("NEOMAX_PROJECTS_CONFIG")
    canonical_state = not _config.os.environ.get(
        "NEOMAX_HOME"
    ) and _config.os.path.realpath(PROJECTS_FILE) == _config.os.path.realpath(
        DEFAULT_PROJECTS_FILE
    )
    if explicit_local or canonical_state:
        projects.update(_project_map(explicit_local or LOCAL_PROJECTS_FILE))
    projects.update(_project_map(PROJECTS_FILE))
    return projects


def project_of(path):
    """Return the registered project owning a path or repository basename."""
    from . import config as _config

    if not path:
        return None
    projs = load_projects()
    ap = _config.os.path.abspath(_config.os.path.expanduser(str(path)))
    (best, best_len) = (None, 0)
    for name, cfg in projs.items():
        root = _config.os.path.abspath(_config.os.path.expanduser(cfg.get("root", "")))
        if (
            root
            and (ap == root or ap.startswith(root + _config.os.sep))
            and (len(root) > best_len)
        ):
            (best, best_len) = (name, len(root))
    if best:
        return best
    base = _config.os.path.basename(str(path).rstrip("/"))
    for name, cfg in projs.items():
        if base in (cfg.get("repos") or []):
            return name
    return None


def save_projects(projs):
    """Persist the per-user project registry atomically."""
    from . import config as _config

    _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
    tmp = "%s.tmp.%d" % (PROJECTS_FILE, _config.os.getpid())
    with open(tmp, "w") as f:
        _config.json.dump(projs or {}, f, indent=2)
    _config.os.replace(tmp, PROJECTS_FILE)


def project_slug(s):
    """A registry-safe project name from any label: lowercase, alnum only (DebateX -> debatex)."""
    return "".join((c for c in str(s).lower() if c.isalnum())) or "project"


def discover_project_repos(root):
    """Discover a single git root or immediate child repositories."""
    from . import config as _config

    if _config.os.path.exists(_config.os.path.join(root, ".git")):
        return ["."]
    try:
        repos = [
            name
            for name in sorted(_config.os.listdir(root))
            if _config.os.path.exists(_config.os.path.join(root, name, ".git"))
        ]
    except OSError:
        repos = []
    return repos or ["."]


def ensure_launch_project(root=None):
    """Register the launch directory without writing into the project itself."""
    from . import config as _config

    root = _config.os.path.abspath(
        _config.os.path.expanduser(
            root or _config.os.environ.get("NEOMAX_PROJECT_ROOT") or _config.os.getcwd()
        )
    )
    if not _config.os.path.isdir(root) or root in (
        _config.os.path.abspath(_config.os.sep),
        _config.os.path.abspath(_config.HOME),
    ):
        return project_of(root)
    existing = project_of(root)
    if existing:
        return existing
    base = project_slug(
        _config.os.environ.get("NEOMAX_PROJECT_NAME")
        or _config.os.path.basename(root.rstrip(_config.os.sep))
    )
    projects = load_projects()
    name = base
    if name in projects and _config.os.path.realpath(
        projects[name].get("root", "")
    ) != _config.os.path.realpath(root):
        name = base + _config.hashlib.sha256(root.encode()).hexdigest()[:6]
    used_prefixes = {str(cfg.get("branch_prefix") or "") for cfg in projects.values()}
    prefix = name[:4] or "proj"
    if prefix in used_prefixes:
        prefix = (prefix[:3] + _config.hashlib.sha256(root.encode()).hexdigest()[:3])[
            :6
        ]
    cfg = {
        "root": root,
        "repos": discover_project_repos(root),
        "branch_prefix": prefix,
        "brain": "CLAUDE.md",
        "agents": "AGENTS.md",
        "orch_brain": "docs/neomax-orchestrator/ORCHESTRATOR.md",
        "opener": "docs/neomax-orchestrator/ORCHESTRATOR_OPENER.md",
        "planning": "docs/neomax-orchestrator",
        "desc": "%s — project rooted at %s" % (name, root),
        "created": int(_config.time.time()),
        "auto_registered": True,
    }
    (ok, registered) = register_project(name, cfg)
    return registered if ok else project_of(root)


def is_unregistered_project_dir(path):
    from . import config as _config

    if not path:
        return False
    root = _config.os.path.abspath(_config.os.path.expanduser(str(path)))
    return (
        _config.os.path.isdir(root)
        and root
        not in (
            _config.os.path.abspath(_config.os.sep),
            _config.os.path.abspath(_config.HOME),
        )
        and (project_of(root) is None)
    )


def register_project(name, cfg, overwrite=False):
    """Add/replace a project in the registry. Returns (ok, message)."""
    from . import config as _config

    name = project_slug(name)
    projs = load_projects()
    if name in projs and (not overwrite):
        return (
            False,
            "a project named '%s' is already registered (use --force to overwrite)"
            % name,
        )
    root = _config.os.path.abspath(_config.os.path.expanduser(cfg.get("root") or ""))
    if not root:
        return (False, "a project needs a --root")
    for other, ocfg in projs.items():
        if other == name:
            continue
        oroot = _config.os.path.abspath(
            _config.os.path.expanduser(ocfg.get("root") or "")
        )
        if oroot and (
            root == oroot
            or root.startswith(oroot + _config.os.sep)
            or oroot.startswith(root + _config.os.sep)
        ):
            return (
                False,
                "root %s overlaps the existing project '%s' (%s)"
                % (root, other, oroot),
            )
    cfg = dict(cfg)
    cfg["root"] = root
    projs[name] = cfg
    save_projects(projs)
    return (True, name)


def cmd_project_register(argv):
    """Register (or update) a project so the orchestrator + dashboard treat this folder as a
    first-class project — runs/sessions/sub-agents under its root get tagged `project=<name>`.
       neomax project-register --name <name> --root <path> [--prefix p] [--repos a,b]
                 [--desc "..."] [--brain CLAUDE.md] [--agents AGENTS.md]
                 [--orch-brain docs/neomax-orchestrator/ORCHESTRATOR.md]
                 [--opener docs/neomax-orchestrator/ORCHESTRATOR_OPENER.md]
                 [--planning docs/neomax-orchestrator] [--force]
       neomax project-register --unregister <name>
    Defaults: root=cwd, name=slug(basename root), prefix=name[:4], repos=["."]."""
    from . import config as _config
    from . import models as _models

    opts = {}
    i = 0
    flags = {
        "--name",
        "--root",
        "--prefix",
        "--repos",
        "--desc",
        "--brain",
        "--agents",
        "--orch-brain",
        "--opener",
        "--planning",
        "--unregister",
    }
    force = False
    unregister = None
    while i < len(argv):
        a = argv[i]
        if a == "--force":
            force = True
            i += 1
            continue
        if a == "--unregister" and i + 1 < len(argv):
            unregister = argv[i + 1]
            i += 2
            continue
        if a in flags and i + 1 < len(argv):
            opts[a[2:]] = argv[i + 1]
            i += 2
            continue
        i += 1
    if unregister:
        name = project_slug(unregister)
        projs = load_projects()
        if name not in projs:
            _models.err("neomax: no registered project named '%s'" % name)
            _config.sys.exit(1)
        del projs[name]
        save_projects(projs)
        print(
            "project-unregister: removed '%s' (its files on disk are left untouched)"
            % name
        )
        return
    root = _config.os.path.abspath(
        _config.os.path.expanduser(opts.get("root") or _config.os.getcwd())
    )
    name = project_slug(opts.get("name") or _config.os.path.basename(root.rstrip("/")))
    repos = [r.strip() for r in (opts.get("repos") or ".").split(",") if r.strip()] or [
        "."
    ]
    cfg = {
        "root": root,
        "repos": repos,
        "branch_prefix": opts.get("prefix") or name[:4] or "prj",
        "brain": opts.get("brain") or "CLAUDE.md",
        "agents": opts.get("agents") or "AGENTS.md",
        "orch_brain": opts.get("orch-brain")
        or "docs/neomax-orchestrator/ORCHESTRATOR.md",
        "opener": opts.get("opener")
        or "docs/neomax-orchestrator/ORCHESTRATOR_OPENER.md",
        "planning": opts.get("planning") or "docs/neomax-orchestrator",
        "desc": opts.get("desc") or "%s — root %s" % (name, root),
        "created": int(_config.time.time()),
    }
    (ok, msg) = register_project(name, cfg, overwrite=force)
    if not ok:
        _models.err("neomax project-register: " + msg)
        _config.sys.exit(2)
    print(
        "project-register: '%s' registered → root %s, branch namespace `%s/`. Runs/sessions/sub-agents under this root now tag `project=%s` (shown in `neomax status`/dashboard)."
        % (name, root, cfg["branch_prefix"], name)
    )
