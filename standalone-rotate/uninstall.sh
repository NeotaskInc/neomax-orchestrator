#!/usr/bin/env bash
# crotate uninstaller — reverses install.sh. Account profile dirs, credentials and
# ~/.crotate state are KEPT (they hold real logins); remove those by hand if wanted.
set -uo pipefail

LOCAL_BIN="$HOME/.local/bin"
CLAUDE_DIR="$HOME/.claude"
ZSHRC="$HOME/.zshrc"
MARK_BEGIN="# >>> crotate >>>"
MARK_END="# <<< crotate <<<"
PLIST="$HOME/Library/LaunchAgents/com.crotate.tick.plist"

say() { printf '\033[1;36m[crotate]\033[0m %s\n' "$1"; }

# launchd tick agent
if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  say "removed launchd tick agent"
fi

# binary + aliases symlinks
[ -L "$LOCAL_BIN/crotate" ] && rm -f "$LOCAL_BIN/crotate" && say "removed $LOCAL_BIN/crotate"
[ -L "$LOCAL_BIN/crotate-aliases.zsh" ] && rm -f "$LOCAL_BIN/crotate-aliases.zsh" \
  && say "removed $LOCAL_BIN/crotate-aliases.zsh"

# /rotate command symlinks (only ours — symlinks into this repo)
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in "$CLAUDE_DIR/commands/rotate.md" "$HOME"/.codex*/prompts/rotate.md; do
  if [ -L "$f" ] && [[ "$(readlink "$f")" == "$here"/* ]]; then
    rm -f "$f"
    say "removed $f"
  fi
done

# Stop hook entry in settings.json
SETTINGS="$CLAUDE_DIR/settings.json"
if [ -f "$SETTINGS" ]; then
  python3 - "$SETTINGS" "$LOCAL_BIN/crotate usage-hook" <<'PY'
import json, os, sys
path, stop_cmd = sys.argv[1], sys.argv[2]
try:
    with open(path) as f: data = json.load(f)
except (OSError, ValueError):
    sys.exit(0)
changed = False
for grp in (data.get("hooks", {}).get("Stop") or []):
    inner = grp.get("hooks") or []
    kept = [h for h in inner if h.get("command") != stop_cmd]
    if len(kept) != len(inner):
        grp["hooks"] = kept
        changed = True
if changed:
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f, indent=2)
    os.replace(tmp, path)
    print("[crotate] removed Stop hook")
PY
fi

# zshrc block
if [ -f "$ZSHRC" ] && grep -qF "$MARK_BEGIN" "$ZSHRC"; then
  python3 - "$ZSHRC" "$MARK_BEGIN" "$MARK_END" <<'PY'
import re, sys
p, b, e = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(p).read()
s = re.sub(r"\n*" + re.escape(b) + r".*?" + re.escape(e) + r"\n*", "\n", s, flags=re.S)
open(p, "w").write(s)
PY
  say "removed ~/.zshrc crotate block"
fi

say "done. Kept: account profile dirs (~/.claude-acct*, ~/.codex-acct*) + ~/.crotate state."
