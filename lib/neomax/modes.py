"""Mode discovery and command output."""


def orch_modes_lines(indent="  "):
    """The launch-mode map, rendered COMPACTLY from ORCH_MODES for the session opener.

    Rendered — never re-typed — so the opener, `neomax modes`, and the dashboard's Quick
    Actions can't drift apart (house rule: one source of truth)."""
    from . import mode_catalog as _mode_catalog

    out = []
    for mo in _mode_catalog.ORCH_MODES:
        out.append(
            "%s%-24s %s → %s" % (indent, mo["cmd"], mo["orchestrator"], mo["workers"])
        )
    return "\n".join(out)


def cmd_modes(argv):
    """The orchestration MODES + how to start each, plus account/login commands.
    `--json` for the dashboard; plain for a CLI cheat-sheet."""
    from . import config as _config
    from . import mode_catalog as _mode_catalog

    if "--json" in argv:
        print(
            _config.json.dumps(
                {
                    "modes": _mode_catalog.ORCH_MODES,
                    "account_commands": _mode_catalog.ACCOUNT_CMDS,
                    "launch_dir": _config.os.getcwd(),
                }
            )
        )
        return
    print(
        "Neomax orchestration — start a session from the project directory you want to control:\n"
    )
    cur_sec = None
    for mo in _mode_catalog.ORCH_MODES:
        sec = mo.get("section", "Start an orchestrator session")
        if sec != cur_sec:
            print("  [%s]" % sec)
            cur_sec = sec
        print("  %-26s  %s" % (mo["cmd"], mo["title"]))
        print(
            "  %-26s    %s → %s · %s"
            % ("", mo["orchestrator"], mo["workers"], mo["why"])
        )
    print("\naccounts & tools:")
    for c in _mode_catalog.ACCOUNT_CMDS:
        print("  %-34s %s" % (c["cmd"], c["what"]))


def _new_project_directive(root, name, cfg):
    prefix = cfg.get("branch_prefix", name[:4] or "proj")
    return (
        '\n  NEW PROJECT: this launch directory was registered locally as `%s` at %s with branch prefix `%s/`. Inspect the directory before broad delegation. If its purpose and rules are already clear from existing files, proceed without interrogating the operator. If important details are missing, ask one short round about purpose, repository layout, constraints, and branch naming. At your discretion, create project-owned `CLAUDE.md`, `AGENTS.md`, and `docs/neomax-orchestrator/ORCHESTRATOR_OPENER.md`; use `project/PROJECT-INSTRUCTIONS.example.md` as a shape, never overwrite existing instructions, and never put machine-specific paths into tracked files. Refine the local registry with `neomax project-register --name %s --root "%s" --prefix %s --repos <list> --force` when discovery finds a multi-repository layout. Project setup must not block an already-clear task unnecessarily.\n'
        % (name, root, prefix, name, root, prefix)
    )
