#!/usr/bin/env bash
# crotate installer — the ROTATE FEATURE of neomax-orchestrator, standalone.
# Installs multi-account storage + the /rotate command for Claude Code and Codex
# WITHOUT neomax / the orchestrator. Idempotent: safe to re-run after `git pull`.
#
#   ./install.sh                     # install for the current user (3 accounts)
#   CROTATE_ACCOUNTS=5 ./install.sh  # set up 5 Claude account profile dirs
#   CROTATE_CODEX_ACCOUNTS=2 ./install.sh
#
# What it does (all reversible via ./uninstall.sh):
#   - symlinks crotate into ~/.local/bin (and ensures ~/.local/bin is on PATH)
#   - creates ~/.claude-acctN / ~/.codex-acctN profile dirs (config shared from acct 1)
#   - installs the /rotate slash command (Claude) + /rotate prompt (every Codex profile)
#   - merges a Stop hook (`crotate usage-hook`) into ~/.claude/settings.json
#   - installs a launchd tick agent (rotates an armed, already-rate-limited session)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"
CLAUDE_DIR="$HOME/.claude"
CODEX_DIR="$HOME/.codex"
ACCOUNTS="${CROTATE_ACCOUNTS:-3}"
CODEX_ACCOUNTS="${CROTATE_CODEX_ACCOUNTS:-$ACCOUNTS}"
ZSHRC="$HOME/.zshrc"
MARK_BEGIN="# >>> crotate >>>"
MARK_END="# <<< crotate <<<"
PLIST="$HOME/Library/LaunchAgents/com.crotate.tick.plist"

say() { printf '\033[1;36m[crotate]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[crotate] WARN\033[0m %s\n' "$1"; }

# 1. binary → ~/.local/bin (symlink so `git pull` updates the live tool)
mkdir -p "$LOCAL_BIN"
chmod +x "$REPO_DIR/crotate"
ln -sfn "$REPO_DIR/crotate" "$LOCAL_BIN/crotate"
# dynamic per-account shell helpers (claudeN/codexN for EVERY account, sourced from ~/.zshrc)
ln -sfn "$REPO_DIR/crotate-aliases.zsh" "$LOCAL_BIN/crotate-aliases.zsh"
say "linked crotate + crotate-aliases.zsh into $LOCAL_BIN"

if [ ! -d "$CLAUDE_DIR" ]; then
  warn "$CLAUDE_DIR does not exist — install Claude Code and run \`claude\` once (log in to account 1) first."
  mkdir -p "$CLAUDE_DIR"
fi
mkdir -p "$CLAUDE_DIR/commands"

# 2. extra Claude account profile dirs, each sharing account-1's config via symlink
for n in $([ "$ACCOUNTS" -ge 2 ] && seq 2 "$ACCOUNTS"); do
  prof="$HOME/.claude-acct$n"
  mkdir -p "$prof"
  for item in CLAUDE.md settings.json commands plugins; do
    if [ -e "$CLAUDE_DIR/$item" ] && [ ! -e "$prof/$item" ]; then
      ln -sfn "$CLAUDE_DIR/$item" "$prof/$item"
    fi
  done
  say "claude account $n → $prof (config shared from account 1; authenticate: crotate login $n)"
done

# 2b. Codex account profile dirs (CODEX_HOME isolation; auth.json stays per dir)
mkdir -p "$CODEX_DIR/prompts"
for n in $([ "$CODEX_ACCOUNTS" -ge 2 ] && seq 2 "$CODEX_ACCOUNTS"); do
  prof="$HOME/.codex-acct$n"
  mkdir -p "$prof/prompts"
  if [ -f "$CODEX_DIR/config.toml" ] && [ ! -e "$prof/config.toml" ]; then
    cp "$CODEX_DIR/config.toml" "$prof/config.toml"
  fi
  say "codex account $n → $prof (authenticate: crotate login $n --engine codex)"
done

# 3. /rotate slash command (Claude — commands dir is shared into every claude profile)
ln -sfn "$REPO_DIR/claude-rotate.md" "$CLAUDE_DIR/commands/rotate.md"
say "linked /rotate Claude command"

# 3b. /rotate Codex prompt into EVERY codex profile
for cxdir in "$CODEX_DIR" $([ "$CODEX_ACCOUNTS" -ge 2 ] && for n in $(seq 2 "$CODEX_ACCOUNTS"); do echo "$HOME/.codex-acct$n"; done); do
  [ -d "$cxdir" ] || continue
  mkdir -p "$cxdir/prompts"
  ln -sfn "$REPO_DIR/codex-rotate.md" "$cxdir/prompts/rotate.md"
done
say "linked /rotate Codex prompt into every codex profile"

# 4. Stop hook → ~/.claude/settings.json (merge, never clobber): armed auto-rotation
#    re-checks at every turn-end. settings.json is shared into every claude profile.
SETTINGS="$CLAUDE_DIR/settings.json"
python3 - "$SETTINGS" "$LOCAL_BIN/crotate usage-hook" <<'PY'
import json, os, sys
path, stop_cmd = sys.argv[1], sys.argv[2]
try:
    with open(path) as f: data = json.load(f)
except (OSError, ValueError):
    data = {}
hooks = data.setdefault("hooks", {})
arr = hooks.setdefault("Stop", [])
grp = next((g for g in arr if g.get("matcher", "") == ""), None)
if grp is None:
    grp = {"matcher": "", "hooks": []}; arr.append(grp)
inner = grp.setdefault("hooks", [])
if not any(h.get("command") == stop_cmd for h in inner):
    inner.append({"type": "command", "command": stop_cmd, "timeout": 8})
    print("[crotate] added Stop hook")
else:
    print("[crotate] Stop hook already present")
tmp = path + ".tmp"
with open(tmp, "w") as f: json.dump(data, f, indent=2)
os.replace(tmp, path)
PY

# 5. launchd tick agent — model-free safety net: rotates an ARMED session that is
#    already rate-limited (its Stop hook can never fire). Runs `crotate tick` every 60s.
if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.crotate.tick</string>
  <key>ProgramArguments</key><array>
    <string>$LOCAL_BIN/crotate</string><string>tick</string>
  </array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/crotate-tick.log</string>
  <key>StandardErrorPath</key><string>/tmp/crotate-tick.log</string>
</dict></plist>
XML
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST" 2>/dev/null \
    && say "installed + started the rotation tick (launchd, every 60s)" \
    || warn "could not load the launchd tick agent — load manually: launchctl load $PLIST"
fi

# 6. PATH (idempotent block in ~/.zshrc); strip any stale block first, then append
block="$MARK_BEGIN
export PATH=\"\$HOME/.local/bin:\$PATH\"
# claudeN / codexN account helpers — DYNAMIC (one per existing account profile; new accounts auto-appear)
[ -r \"\$HOME/.local/bin/crotate-aliases.zsh\" ] && source \"\$HOME/.local/bin/crotate-aliases.zsh\"
$MARK_END"
if [ -f "$ZSHRC" ] && grep -qF "$MARK_BEGIN" "$ZSHRC"; then
  python3 - "$ZSHRC" "$MARK_BEGIN" "$MARK_END" <<'PY'
import re, sys
p, b, e = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(p).read()
s = re.sub(r"\n*" + re.escape(b) + r".*?" + re.escape(e) + r"\n*", "\n", s, flags=re.S)
open(p, "w").write(s)
PY
  printf '\n%s\n' "$block" >> "$ZSHRC"
  say "refreshed ~/.zshrc crotate block"
else
  printf '\n%s\n' "$block" >> "$ZSHRC"
  say "added PATH block to ~/.zshrc"
fi

say "done. Next:"
say "  1. open a NEW terminal (picks up PATH)"
for n in $([ "$ACCOUNTS" -ge 2 ] && seq 2 "$ACCOUNTS"); do
  say "  2. Claude: 'crotate login $n' then /login inside with account $n (one time)"
done
for n in $([ "$CODEX_ACCOUNTS" -ge 2 ] && seq 2 "$CODEX_ACCOUNTS"); do
  say "  3. Codex:  'crotate login $n --engine codex' to authenticate Codex account $n (one time)"
done
say "  4. 'crotate status' to confirm all accounts; 'crotate portal' for the dashboard"
say "  5. claude1/claude2/... and codex1/codex2/... open a plain session on that account"
say "  6. in any Claude/Codex session: /rotate  (arms hands-off auto-rotation)"
