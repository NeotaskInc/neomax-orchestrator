# Neomax per-account shell helpers — DYNAMIC.
# Sourced from ~/.zshrc (via the managed Neomax block). Defines claudeN / codexN for EVERY account
# that has a profile dir, so adding an account (`cmax N` + /login creates ~/.claude-acctN) makes
# `claudeN` available in every NEW shell automatically — no install edit, no hardcoded 2/3 list.
#
#   acct 1 → ~/.claude        : `claude1` = `cmax 1`   (bare `claude` is a plain, non-orchestrator session)
#   acct N → ~/.claude-acctN  : `claudeN` = `cmax N`
#   codex  : `codexN` = `cdx run N`   (acct 1 = ~/.codex, acct N = ~/.codex-acctN)
#
# Glob qualifiers: (N)=nullglob — no match → loop body never runs (no error); (n)=numeric sort;
# (/)=directories only. Same discovery cmax/neomax use, so this never drifts from the real accounts.

claude1() { cmax 1 "$@"; }
for _cmaxprof in "$HOME"/.claude-acct<2->(Nn/); do
  functions[claude${_cmaxprof##*-acct}]="cmax ${_cmaxprof##*-acct} \"\$@\""
done

codex1() { cdx run 1 "$@"; }
for _cmaxprof in "$HOME"/.codex-acct<2->(Nn/); do
  functions[codex${_cmaxprof##*-acct}]="cdx run ${_cmaxprof##*-acct} \"\$@\""
done

opencode1() { ocx run 1 "$@"; }
for _cmaxprof in "$HOME"/.opencode-acct<2->(Nn/); do
  functions[opencode${_cmaxprof##*-acct}]="ocx run ${_cmaxprof##*-acct} \"\$@\""
done

kimi1() { kmx run 1 "$@"; }
for _cmaxprof in "$HOME"/.kimi-code-acct<2->(Nn/); do
  functions[kimi${_cmaxprof##*-acct}]="kmx run ${_cmaxprof##*-acct} \"\$@\""
done

grok1() { gmx run 1 "$@"; }
for _cmaxprof in "$HOME"/.grok-acct<2->(Nn/); do
  functions[grok${_cmaxprof##*-acct}]="gmx run ${_cmaxprof##*-acct} \"\$@\""
done

unset _cmaxprof
