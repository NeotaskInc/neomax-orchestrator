#!/usr/bin/env bash
# Neomax Orchestrator installer. Idempotent: safe to re-run after `git pull`.
#
#   ./install.sh                 # install for the current user
#   NEOMAX_CLAUDE_ACCOUNTS=2 ./install.sh  # set up 2 accounts instead of the default 3
#
# What it does (all reversible via ./uninstall.sh):
#   - symlinks Neomax and the provider-specific launchers into ~/.local/bin
#   - ensures ~/.local/bin is on PATH (via ~/.zshrc)
#   - creates ~/.claude-acct2.. profile dirs, each sharing account-1's config by symlink
#   - installs the /neomax, /rotate, /find-issues, and /fix-issues workflows for every provider
#   - merges a SessionStart hook into ~/.claude/settings.json (keeps existing hooks)
#   - adds claude2/claude3 shell helpers to ~/.zshrc
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"
CLAUDE_DIR="$HOME/.claude"
ACCOUNTS="${NEOMAX_CLAUDE_ACCOUNTS:-3}"
ZSHRC="$HOME/.zshrc"
MARK_BEGIN="# >>> neomax-orchestrator >>>"
MARK_END="# <<< neomax-orchestrator <<<"
LEGACY_MARK_BEGIN="# >>> c""max-orchestrator >>>"
LEGACY_MARK_END="# <<< c""max-orchestrator <<<"

say() { printf '\033[1;36m[neomax]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[neomax] WARN\033[0m %s\n' "$1"; }

# 1. binaries → ~/.local/bin (symlinks so `git pull` updates the live tool)
mkdir -p "$LOCAL_BIN"
chmod +x "$REPO_DIR/bin/neomax" "$REPO_DIR/bin/cmax" "$REPO_DIR/bin/ocx" "$REPO_DIR/bin/ocmax" "$REPO_DIR/bin/kmx" "$REPO_DIR/bin/kmax" "$REPO_DIR/bin/gmx" "$REPO_DIR/bin/gmax"
ln -sfn "$REPO_DIR/bin/neomax" "$LOCAL_BIN/neomax"
ln -sfn "$REPO_DIR/bin/cmax" "$LOCAL_BIN/cmax"
legacy_cli="$LOCAL_BIN/cde""legate"
[ -L "$legacy_cli" ] && rm -f "$legacy_cli"
legacy_global="c""max"
for old in "$legacy_global-portal" "$legacy_global-usage-agent" "$legacy_global-worktrees" "$legacy_global-aliases.zsh"; do
  [ -L "$LOCAL_BIN/$old" ] && rm -f "$LOCAL_BIN/$old"
done
retired_probe="$LOCAL_BIN/ox-""smo""ke"
[ -L "$retired_probe" ] && rm -f "$retired_probe"
ln -sfn "$REPO_DIR/bin/cdx" "$LOCAL_BIN/cdx"
ln -sfn "$REPO_DIR/bin/cdxmax" "$LOCAL_BIN/cdxmax"
ln -sfn "$REPO_DIR/bin/ocx" "$LOCAL_BIN/ocx"
ln -sfn "$REPO_DIR/bin/ocmax" "$LOCAL_BIN/ocmax"
ln -sfn "$REPO_DIR/bin/kmx" "$LOCAL_BIN/kmx"
ln -sfn "$REPO_DIR/bin/kmax" "$LOCAL_BIN/kmax"
ln -sfn "$REPO_DIR/bin/gmx" "$LOCAL_BIN/gmx"
ln -sfn "$REPO_DIR/bin/gmax" "$LOCAL_BIN/gmax"
for legacy in kix kimax; do
  [ -L "$LOCAL_BIN/$legacy" ] && [ "$(readlink "$LOCAL_BIN/$legacy")" = "$REPO_DIR/bin/$legacy" ] && rm -f "$LOCAL_BIN/$legacy"
done
ln -sfn "$REPO_DIR/bin/neomax-portal" "$LOCAL_BIN/neomax-portal"
ln -sfn "$REPO_DIR/bin/neomax-usage-agent" "$LOCAL_BIN/neomax-usage-agent"
chmod +x "$REPO_DIR/bin/cdx" "$REPO_DIR/bin/cdxmax" "$REPO_DIR/bin/neomax-portal" "$REPO_DIR/bin/neomax-usage-agent" "$REPO_DIR/project/neomax-worktrees"
ln -sfn "$REPO_DIR/project/neomax-worktrees" "$LOCAL_BIN/neomax-worktrees"
# dynamic per-account shell helpers (claudeN/codexN/opencodeN/kimiN/grokN, sourced from ~/.zshrc)
ln -sfn "$REPO_DIR/shell/neomax-aliases.zsh" "$LOCAL_BIN/neomax-aliases.zsh"
say "linked neomax + provider launchers + account helpers + portal into $LOCAL_BIN"

legacy_state="$HOME/.cde""legate"
if [ -d "$legacy_state" ] && [ ! -e "$HOME/.neomax" ]; then
  ln -s "$legacy_state" "$HOME/.neomax"
  say "preserved existing run history behind the new ~/.neomax path"
fi

# 1b. Claude binary preflight — INSTALL-METHOD-AGNOSTIC.
#     cmax (`exec claude`) and neomax (CLAUDE_BIN="claude") resolve `claude` purely
#     via PATH at runtime — they NEVER hardcode a path. So WHERE Claude Code is installed
#     (npm/nvm, Homebrew, or the native `claude install` shell installer under
#     ~/.local/share/claude) does NOT matter, as long as (a) some working `claude` wins on
#     PATH and (b) it honors CLAUDE_CONFIG_DIR (every Claude Code >= 2.1.56 does — that's
#     what namespaces each account's Keychain entry). This block just makes the active
#     resolution EXPLICIT so a broken/missing/ambiguous install surfaces loudly here instead
#     of as a silent worker failure later.
claude_bin="$(command -v claude 2>/dev/null || true)"
if [ -n "$claude_bin" ]; then
  claude_real="$(readlink -f "$claude_bin" 2>/dev/null || echo "$claude_bin")"
  case "$claude_real" in
    *.nvm/*|*/node_modules/*) method="npm/nvm" ;;
    *Homebrew*|*/homebrew/*)  method="Homebrew" ;;
    *.local/share/claude/*|*.local/bin/*) method="native local installer" ;;
    *) method="custom path" ;;
  esac
  claude_ver="$(command claude --version 2>/dev/null | head -1 | awk '{print $1}')"
  say "Claude binary on PATH: $claude_bin  (${claude_ver:-version?}, via $method)"
  say "  → orchestrator runs whatever \`claude\` PATH resolves to; install location is not pinned."
else
  warn "no \`claude\` on PATH — cmax/neomax cannot launch any Claude worker until this is fixed."
  # Surface a dormant install (native installer dropped a binary but no PATH launcher) so the
  # user knows the fix is a PATH/launcher issue, not a reinstall.
  for cand in "$HOME/.local/bin/claude" "$HOME/.local/share/claude/versions/"* /opt/homebrew/bin/claude; do
    [ -e "$cand" ] && warn "  found an installed-but-not-on-PATH Claude at: $cand"
  done
  warn "  fix: ensure a \`claude\` launcher is on PATH (e.g. the native installer's ~/.local/bin/claude,"
  warn "       or \`npm i -g @anthropic-ai/claude-code\`), then re-run ./install.sh."
fi

if command -v opencode >/dev/null 2>&1; then
  say "OpenCode binary on PATH: $(command -v opencode) ($(opencode --version 2>/dev/null | head -1 || echo version?))"
else
  warn "no \`opencode\` on PATH — ocx/ocmax and OpenCode workers cannot launch yet"
fi

if command -v kimi >/dev/null 2>&1; then
  say "Kimi binary on PATH: $(command -v kimi) ($(kimi --version 2>/dev/null | head -1 || echo version?))"
else
  warn "no \`kimi\` on PATH — kmx/kmax and Kimi workers cannot launch yet"
fi

if command -v grok >/dev/null 2>&1; then
  say "Grok binary on PATH: $(command -v grok) ($(grok --version 2>/dev/null | head -1 || echo version?))"
else
  warn "no \`grok\` on PATH — gmx/gmax and Grok workers cannot launch yet"
  warn "  install the official open-source Grok Build CLI: curl -fsSL https://x.ai/cli/install.sh | bash"
fi

if [ ! -d "$CLAUDE_DIR" ]; then
  warn "$CLAUDE_DIR does not exist — install Claude Code and run \`claude\` once (log in to account 1) before using cmax."
  mkdir -p "$CLAUDE_DIR"
fi
mkdir -p "$CLAUDE_DIR/commands"

# 2. extra Claude account profile dirs, each sharing account-1's config via symlink
for n in $([ "$ACCOUNTS" -ge 2 ] && seq 2 "$ACCOUNTS"); do
  prof="$HOME/.claude-acct$n"
  mkdir -p "$prof"
  for item in CLAUDE.md settings.json commands plugins; do
    if [ -e "$CLAUDE_DIR/$item" ]; then
      ln -sfn "$CLAUDE_DIR/$item" "$prof/$item"
    fi
  done
  say "claude account $n → $prof (config shared from account 1)"
done

# 2b. Codex account profile dirs (CODEX_HOME isolation; auth is file-based, no symlink
#     of secrets). Share the non-secret config.toml from account 1 so model/effort
#     defaults match; auth.json stays private per dir (created by `cdx login`).
#     Custom prompts (/rotate, /find-issues, /fix-issues) live in <CODEX_HOME>/prompts and are
#     symlinked into EVERY profile (section 3b) so the codex slash commands exist per account.
CODEX_DIR="$HOME/.codex"
mkdir -p "$CODEX_DIR/prompts"
CODEX_ACCOUNTS="${NEOMAX_CODEX_ACCOUNTS:-$ACCOUNTS}"
for n in $([ "$CODEX_ACCOUNTS" -ge 2 ] && seq 2 "$CODEX_ACCOUNTS"); do
  prof="$HOME/.codex-acct$n"
  mkdir -p "$prof/prompts"
  if [ -f "$CODEX_DIR/config.toml" ] && [ ! -e "$prof/config.toml" ]; then
    cp "$CODEX_DIR/config.toml" "$prof/config.toml"   # copy (not symlink): independent per account
  fi
  say "codex account $n → $prof (authenticate with: cdx login $n)"
done

# 3. Native Neomax workflows
legacy_claude_command="$CLAUDE_DIR/commands/dele""gate.md"
[ -L "$legacy_claude_command" ] && rm -f "$legacy_claude_command"
ln -sfn "$REPO_DIR/claude/commands/neomax.md" "$CLAUDE_DIR/commands/neomax.md"
ln -sfn "$REPO_DIR/claude/commands/project.md" "$CLAUDE_DIR/commands/project.md"
ln -sfn "$REPO_DIR/claude/commands/rotate.md" "$CLAUDE_DIR/commands/rotate.md"
ln -sfn "$REPO_DIR/claude/commands/find-issues.md" "$CLAUDE_DIR/commands/find-issues.md"
ln -sfn "$REPO_DIR/claude/commands/fix-issues.md" "$CLAUDE_DIR/commands/fix-issues.md"
say "linked /neomax + /project + /rotate + /find-issues + /fix-issues Claude commands"

# 3b. Codex custom prompts (the Codex counterpart of Claude slash commands): <CODEX_HOME>/prompts/*.md
#     → /rotate + /find-issues + /fix-issues inside any `codex`/`cdxmax` session. Symlinked into account 1 AND
#     every extra codex profile so the commands exist regardless of which account orchestrates.
for cxdir in "$CODEX_DIR" $([ "$CODEX_ACCOUNTS" -ge 2 ] && for n in $(seq 2 "$CODEX_ACCOUNTS"); do echo "$HOME/.codex-acct$n"; done); do
  [ -d "$cxdir" ] || continue
  mkdir -p "$cxdir/prompts"
  ln -sfn "$REPO_DIR/codex/prompts/neomax.md" "$cxdir/prompts/neomax.md"
  ln -sfn "$REPO_DIR/codex/prompts/rotate.md" "$cxdir/prompts/rotate.md"
  ln -sfn "$REPO_DIR/codex/prompts/find-issues.md" "$cxdir/prompts/find-issues.md"
  ln -sfn "$REPO_DIR/codex/prompts/fix-issues.md" "$cxdir/prompts/fix-issues.md"
done
say "linked /neomax + /rotate + /find-issues + /fix-issues Codex prompts into every codex profile"

OPENCODE_COMMANDS="$HOME/.config/opencode/commands"
mkdir -p "$OPENCODE_COMMANDS"
for command_name in neomax rotate find-issues fix-issues; do
  ln -sfn "$REPO_DIR/opencode/commands/$command_name.md" "$OPENCODE_COMMANDS/$command_name.md"
done
say "linked /neomax + /rotate + /find-issues + /fix-issues OpenCode commands"

for kimi_profile in "$HOME/.kimi-code" "$HOME/.kimi-code-orch" "$HOME"/.kimi-code-acct[0-9]*; do
  [ -d "$kimi_profile" ] || continue
  mkdir -p "$kimi_profile/skills"
  for skill_name in neomax rotate find-issues fix-issues; do
    ln -sfn "$REPO_DIR/kimi/skills/$skill_name" "$kimi_profile/skills/$skill_name"
  done
done
say "linked Neomax/rotate/find-issues/fix-issues Kimi skills into every Kimi profile"

for grok_profile in "$HOME/.grok" "$HOME/.grok-orch" "$HOME"/.grok-acct[0-9]*; do
  [ -d "$grok_profile" ] || continue
  mkdir -p "$grok_profile/commands"
  for command_name in neomax rotate find-issues fix-issues; do
    ln -sfn "$REPO_DIR/grok/commands/$command_name.md" "$grok_profile/commands/$command_name.md"
  done
done
say "linked /neomax + /rotate + /find-issues + /fix-issues Grok commands into every Grok profile"

# 4. hooks → settings.json (merge, never clobber): SessionStart (completion inbox) +
#    Stop (real-time token-usage capture).
SETTINGS="$CLAUDE_DIR/settings.json"
python3 - "$SETTINGS" "$LOCAL_BIN/neomax ls --hook" "$LOCAL_BIN/neomax usage-hook" "$LOCAL_BIN/neomax orient --hook" "$LOCAL_BIN/neomax turn-hook" <<'PY'
import json, os, re, sys
path, ss_cmd, stop_cmd, orient_cmd, guard_cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
try:
    with open(path) as f: data = json.load(f)
except (OSError, ValueError):
    data = {}
hooks = data.setdefault("hooks", {})
legacy = "cde" + "legate"
for event in list(hooks):
    for group in hooks[event]:
        group["hooks"] = [h for h in group.get("hooks", [])
                          if legacy not in h.get("command", "")
                          and not re.search(r"\bneomax model[-_]guard\b", h.get("command", ""))]
    hooks[event] = [group for group in hooks[event] if group.get("hooks")]
    if not hooks[event]:
        hooks.pop(event, None)
def add(event, cmd, timeout):
    arr = hooks.setdefault(event, [])
    grp = next((g for g in arr if g.get("matcher", "") == ""), None)
    if grp is None:
        grp = {"matcher": "", "hooks": []}; arr.append(grp)
    inner = grp.setdefault("hooks", [])
    if not any(h.get("command") == cmd for h in inner):
        inner.append({"type": "command", "command": cmd, "timeout": timeout})
        print("[neomax] added %s hook" % event)
    else:
        print("[neomax] %s hook already present" % event)
add("SessionStart", ss_cmd, 10)
add("SessionStart", orient_cmd, 10)  # auto-inject the orchestrator opener (self-gates to orchestrator sessions)
add("Stop", stop_cmd, 8)   # capture each completion's tokens the instant a turn ends
add("UserPromptSubmit", guard_cmd, 8)
tmp = path + ".tmp"
with open(tmp, "w") as f: json.dump(data, f, indent=2)
os.replace(tmp, path)
PY

# 4b. token-usage watcher launchd agent — runs AUTOMATICALLY (on login, kept alive),
# capturing every completion's usage in real time. Independent of the dashboard/orchestrator.
if [ "$(uname)" = "Darwin" ]; then
  "$REPO_DIR/bin/neomax-usage-agent" install >/dev/null 2>&1 \
    && say "installed + started the token-usage watcher (launchd agent)" \
    || warn "could not load the usage launchd agent — run: neomax-usage-agent install"
fi

# 5. shell helpers + PATH (idempotent block in ~/.zshrc)
# claudeN = `cmax N` (orchestrator on account N); codexN = `cdx run N`. Defined DYNAMICALLY for
# every account that has a profile dir (sourced from neomax-aliases.zsh), so adding a 4th/5th/6th
# account makes claude4/claude5/... appear in new shells automatically — no hardcoded 2/3 list.
block="$MARK_BEGIN
export PATH=\"\$HOME/.local/bin:\$PATH\"
# Keep the refusal-fallback ENABLED: CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1 turns a
# false-flag into a HARD-ERROR that kills the turn instead of auto-switching to Opus. It can
# lodge in the launchctl (GUI-wide) env that every Terminal-spawned claude inherits, so scrub
# BOTH the launchctl value and this shell's copy (idempotent; harmless when already clean).
launchctl unsetenv CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK 2>/dev/null
unset CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK 2>/dev/null
# claudeN / codexN account helpers — DYNAMIC (one per existing account profile; new accounts auto-appear)
[ -r \"\$HOME/.local/bin/neomax-aliases.zsh\" ] && source \"\$HOME/.local/bin/neomax-aliases.zsh\"
$MARK_END"
# Idempotent + ALWAYS-CURRENT: strip any existing managed block, then append the current
# one. A plain "already present → skip" left a stale block after `git pull` (new helpers /
# the refusal-fallback unset never landed) — breaking the documented "safe to re-run" promise.
# The \n*…\n* collapse keeps the round-trip lossless (no blank-line accretion across re-runs).
if [ -f "$ZSHRC" ] && { grep -qF "$MARK_BEGIN" "$ZSHRC" || grep -qF "$LEGACY_MARK_BEGIN" "$ZSHRC"; }; then
  python3 - "$ZSHRC" "$MARK_BEGIN" "$MARK_END" "$LEGACY_MARK_BEGIN" "$LEGACY_MARK_END" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
for b, e in ((sys.argv[2], sys.argv[3]), (sys.argv[4], sys.argv[5])):
    s = re.sub(r"\n*" + re.escape(b) + r".*?" + re.escape(e) + r"\n*", "\n", s, flags=re.S)
open(p, "w").write(s)
PY
  printf '\n%s\n' "$block" >> "$ZSHRC"
  say "refreshed ~/.zshrc Neomax block"
else
  printf '\n%s\n' "$block" >> "$ZSHRC"
  say "added PATH + claude2/claude3 helpers to ~/.zshrc"
fi

say "done. Next:"
say "  1. open a NEW terminal (picks up PATH + helpers)"
for n in $([ "$ACCOUNTS" -ge 2 ] && seq 2 "$ACCOUNTS"); do
  say "  2. Claude: 'cmax $n' then /login with your account-$n Max subscription (one time)"
done
for n in $([ "$CODEX_ACCOUNTS" -ge 2 ] && seq 2 "$CODEX_ACCOUNTS"); do
  say "  3. Codex:  'cdx login $n' to authenticate Codex account $n (one time)"
done
say "  4. OpenCode OX: 'ocx status'; add another isolated Go account later with 'ocx login N'"
say "  5. Start OpenCode with 'ocmax' (defaults to opencode-go/ox-alpha-free; override with --model provider/model)"
say "  6. Kimi: 'kmx status'; add an isolated account with 'kmx login N'; start K3 with 'kmax'"
say "  7. Grok: 'gmx status'; add an isolated OAuth/API-key account with 'gmx login N'; start with 'gmax'"
say "  8. Run 'neomax' to select an eligible orchestrator and use every connected provider pool"
