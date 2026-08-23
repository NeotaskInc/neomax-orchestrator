# standalone-rotate — the cmax rotate feature, without the orchestrator

Multi-account storage + the `/rotate` command for **Claude Code** and **Codex**,
extracted from neomax-orchestrator to run **standalone**: no `neomax`, no worker
dispatch, no run ledger. One python-stdlib tool (`crotate`), one installer, and a
read-only localhost portal.

State lives in `~/.crotate` — fully independent of `~/.neomax`, so this can be
installed alongside (or instead of) the full orchestrator.

## Install

```sh
cd standalone-rotate
./install.sh                     # 3 Claude + 3 Codex account slots by default
CROTATE_ACCOUNTS=5 ./install.sh  # more slots
```

Then authenticate each extra account once:

```sh
crotate login 2                  # Claude account 2 (run /login inside, then exit)
crotate login 2 --engine codex   # Codex account 2 (browser login)
crotate status                   # confirm the board
```

## What you get

- **`claude1`…`claude10` / `codex1`…`codex10` in the terminal** — 10 account
  slots per engine by default: type `claude4` and a plain Claude Code session
  opens on account 4. An account that was never ADDED fails cleanly instead of
  opening a logged-out session — `claude10` → *"no claude account 10 added —
  authenticate it first: crotate login 10"*. Adding an account is explicit:
  `crotate login N` (creates the profile sharing account-1's settings/commands,
  so /rotate and the hook follow it, and opens the login). Args pass through:
  `claude3 -r` resumes on account 3. Accounts ≥11 appear automatically once
  their profile dir exists.
- **`/rotate` in any Claude session** — swaps this session's account in place with
  the freshest other account (a true SWAP, no duplicate identities, **no /login**)
  and ARMS hands-off auto-rotation: the Stop hook re-checks at every turn-end and
  rotates when the account hits **97% 5h or 99% weekly** (`--threshold` to change).
- **`/rotate` in any Codex session** — same, via auth.json exchange; auto-rotation
  is driven by the launchd tick (Codex has no hooks).
- **Launchd tick (every 60s)** — the model-free safety net: a session that already
  hit its wall never ends a turn, so its Stop hook can't fire; the tick rotates it
  anyway. Also the path that works with zero AI tokens: `crotate session-rotate`
  in a terminal (or `!crotate session-rotate` in the Claude prompt box).
- **Portal** — `crotate portal` → http://127.0.0.1:8787 (opens your browser;
  `--no-open` to suppress) — read-only dashboard:
  account cards (5h/7d bars, reset ETAs, cooldown/armed/paused/active badges) and
  the recent-rotation log for both engines. Auto-refreshes. It needs no
  orchestrator — plain `claudeN`/`codexN` sessions are exactly what it tracks
  (Claude usage via each account's own API token; Codex via the rollouts those
  sessions write).
- **Manual tools** — `crotate rotate-auth <dest> --from <src> [--swap|--restore]`,
  `crotate pause N` / `unpause N`, `crotate log`, `crotate disarm`.

## How it works (same mechanisms as cmax)

- Claude accounts are isolated by `CLAUDE_CONFIG_DIR` (`~/.claude` = acct 1,
  `~/.claude-acctN`), which namespaces the macOS Keychain item per profile; Codex
  by `CODEX_HOME` (file-based `auth.json`). Accounts are discovered by glob —
  adding an account is just `crotate login 4`.
- A rotation **swaps** the two profiles' stored credentials (keychain +
  `.credentials.json` + `.claude.json` identity for Claude; `auth.json` for Codex),
  so the spent account trades places with the fresh one and cools **until its real
  window reset**. The live session adopts the new account at its next token refresh.
- Target choice is **5h-first** among usable accounts (logged in, not paused /
  cooled / ≥99% weekly, under the 95% 5h ceiling), with an anti-herd claim so
  concurrent rotations spread, and a gentle nudge toward the account whose weekly
  resets soonest (use-it-or-lose-it).
- Usage: Claude = per-account OAuth usage API with the account's own token
  (expired idle tokens are refreshed in place); Codex = rollout rate-limit events.
- Everything degrades gracefully: malformed state files, missing dirs, or a dead
  token never crash a command. Credentials are backed up (0600, `~/.crotate/
  auth-backups`) before every swap; `crotate rotate-auth --restore N` undoes one.

## Uninstall

```sh
./uninstall.sh   # removes the binary, hooks, commands, launchd agent, PATH block
                 # KEEPS account profiles + ~/.crotate state (real logins live there)
```

## Tests

Hermetic (stubbed profiles/keychain/network): `python3 test_crotate.py` —
also wired into the repo gate (`npm test`).
