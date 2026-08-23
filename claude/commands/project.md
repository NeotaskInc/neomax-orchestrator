---
description: Switch the orchestrator's focus to any locally registered project
---

You are the Neomax fleet orchestrator. The user wants to focus delegation on a specific project: **$ARGUMENTS** (one of the registered projects; if blank or "list", show the options).

Do this:

1. Run `neomax projects` to get the registry (each project's `root`, `repos`, `branch_prefix`, `planning`). If `$ARGUMENTS` is empty or "list", print the projects (name · root · branch prefix) and stop.

2. Resolve `$ARGUMENTS` against the live registry, case-insensitively, accepting an unambiguous prefix. If it does not match, list the available projects.

3. **Switch focus to that project** for all subsequent delegation in this session:
   - **`cd` into that project's root** (or the specific repo under it) **before every `neomax` dispatch** — neomax tags the run `project=<name>` from the dispatch cwd and creates the worktree from the git repo there, so this is what keeps the work correctly tagged, branched, and isolated.
   - Use that project's **branch prefix** (`<prefix>/<slug>`) — never another project's namespace.
   - If you haven't already this session, read that project's brain: its root `CLAUDE.md` (+ `AGENTS.md`) and its planning home `<root>/<planning>/` (the `00-README.md` and `ORCHESTRATOR.md`). Read the relevant per-repo/per-subdir `AGENTS.md` before delegating into it.
   - All the delegation rules are unchanged — especially: **brief workers with complete, planned mini-specs** (objective · context · scope · ground rules · approach/phases · acceptance · do-not-touch), never one-liners.

4. Confirm in one line: "Focused on <name> — root <root>, branch prefix <prefix>/, planning <root>/<planning>. Dispatches will tag project=<name>." Then continue with whatever the user asked (or wait).

Note: focus is per-dispatch (it follows the cwd you delegate from), so you can switch projects mid-session simply by `cd`-ing to a different project before the next `neomax` call. Keep each project's work on its own branches/worktrees — never mix two projects' work on one branch.
