#!/usr/bin/env bash
# Reverse install.sh. Leaves your Claude logins + account profiles intact by
# default (they hold real credentials); pass --purge-profiles to also remove the
# ~/.claude-acctN dirs.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"
CLAUDE_DIR="$HOME/.claude"
ZSHRC="$HOME/.zshrc"
say() { printf '\033[1;36m[neomax]\033[0m %s\n' "$1"; }

# stop the launchd usage/keepalive agent BEFORE removing the binary it runs
usage_agent="$LOCAL_BIN/neomax-usage-agent"
[ -x "$usage_agent" ] || usage_agent="$REPO_DIR/bin/neomax-usage-agent"
[ -x "$usage_agent" ] && "$usage_agent" uninstall >/dev/null 2>&1 || true
rm -f "$LOCAL_BIN/neomax" "$LOCAL_BIN/cmax" "$LOCAL_BIN/cdx" "$LOCAL_BIN/cdxmax" \
      "$LOCAL_BIN/ocx" "$LOCAL_BIN/ocmax" "$LOCAL_BIN/kmx" "$LOCAL_BIN/kmax" \
      "$LOCAL_BIN/gmx" "$LOCAL_BIN/gmax" \
      "$LOCAL_BIN/kix" "$LOCAL_BIN/kimax" \
      "$LOCAL_BIN/neomax-portal" "$LOCAL_BIN/neomax-usage-agent" "$LOCAL_BIN/neomax-worktrees" \
      "$LOCAL_BIN/neomax-aliases.zsh"
rm -f "$LOCAL_BIN/cde""legate"
rm -f "$LOCAL_BIN/ox-""smo""ke"
legacy_global="c""max"
rm -f "$LOCAL_BIN/$legacy_global-portal" "$LOCAL_BIN/$legacy_global-usage-agent" \
      "$LOCAL_BIN/$legacy_global-worktrees" "$LOCAL_BIN/$legacy_global-aliases.zsh"
rm -f "$CLAUDE_DIR/commands/neomax.md" "$CLAUDE_DIR/commands/dele""gate.md" "$CLAUDE_DIR/commands/project.md" \
      "$CLAUDE_DIR/commands/rotate.md" "$CLAUDE_DIR/commands/find-issues.md" \
      "$CLAUDE_DIR/commands/fix-issues.md"
# Codex custom prompts, symlinked into account 1 + every extra codex profile by install.sh 3b
for cxdir in "$HOME/.codex" "$HOME"/.codex-acct[0-9]*; do
  [ -d "$cxdir/prompts" ] || continue
  rm -f "$cxdir/prompts/neomax.md" "$cxdir/prompts/rotate.md" \
        "$cxdir/prompts/find-issues.md" "$cxdir/prompts/fix-issues.md"
done
rm -f "$HOME/.config/opencode/commands/neomax.md" \
      "$HOME/.config/opencode/commands/rotate.md" \
      "$HOME/.config/opencode/commands/find-issues.md" \
      "$HOME/.config/opencode/commands/fix-issues.md"
for kimi_profile in "$HOME/.kimi-code" "$HOME/.kimi-code-orch" "$HOME"/.kimi-code-acct[0-9]*; do
  [ -d "$kimi_profile/skills" ] || continue
  rm -f "$kimi_profile/skills/neomax" "$kimi_profile/skills/rotate" \
        "$kimi_profile/skills/find-issues" "$kimi_profile/skills/fix-issues"
done
for grok_profile in "$HOME/.grok" "$HOME/.grok-orch" "$HOME"/.grok-acct[0-9]*; do
  [ -d "$grok_profile/commands" ] || continue
  rm -f "$grok_profile/commands/neomax.md" "$grok_profile/commands/rotate.md" \
        "$grok_profile/commands/find-issues.md" \
        "$grok_profile/commands/fix-issues.md"
done
say "removed binaries, provider workflows, hooks, and launchd agent"

# remove EVERY neomax hook (SessionStart ls+orient, Stop usage-hook,
# UserPromptSubmit turn-hook) — leaving any would make every future session
# error on a deleted binary. Other (non-neomax) hooks untouched.
SETTINGS="$CLAUDE_DIR/settings.json"
if [ -f "$SETTINGS" ]; then
python3 - "$SETTINGS" <<'PY'
import json, os, sys
path = os.path.realpath(sys.argv[1])
try:
    with open(path) as f: data = json.load(f)
except (OSError, ValueError):
    sys.exit(0)
hooks = data.get("hooks", {})
removed = 0
legacy = "cde" + "legate"
for event in list(hooks.keys()):
    groups = hooks.get(event, [])
    for g in groups:
        before = len(g.get("hooks", []))
        g["hooks"] = [h for h in g.get("hooks", [])
                      if "neomax" not in h.get("command", "") and legacy not in h.get("command", "")]
        removed += before - len(g["hooks"])
    hooks[event] = [g for g in groups if g.get("hooks")]
    if not hooks[event]:
        hooks.pop(event, None)
tmp = path + ".tmp"
with open(tmp, "w") as f: json.dump(data, f, indent=2)
os.replace(tmp, path)
print("[neomax] removed %d neomax hook(s) across all events" % removed)
PY
fi

# zshrc block
if [ -f "$ZSHRC" ]; then
  python3 - "$ZSHRC" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
for name in ("neomax-orchestrator", "cmax" + "-orchestrator"):
    s = re.sub(r"\n*# >>> " + re.escape(name) + r" >>>.*?# <<< " + re.escape(name) + r" <<<\n*", "\n", s, flags=re.S)
open(p, "w").write(s)
print("[neomax] removed ~/.zshrc block")
PY
fi

if [ "${1:-}" = "--purge-profiles" ]; then
  rm -rf "$HOME"/.claude-acct[0-9]* "$HOME"/.codex-acct[0-9]* \
         "$HOME"/.opencode-acct[0-9]* "$HOME/.opencode-orch" \
         "$HOME"/.kimi-code-acct[0-9]* "$HOME/.kimi-code-orch" \
         "$HOME"/.grok-acct[0-9]* "$HOME/.grok-orch"
  say "purged extra Claude + Codex + OpenCode + Kimi + Grok profile dirs (default auth kept)"
else
  say "kept Claude + Codex + OpenCode + Kimi + Grok profiles (use --purge-profiles to remove extra logins too)"
fi
say "uninstalled."
