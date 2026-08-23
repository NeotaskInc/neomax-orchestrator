# crotate per-account shell helpers — claudeN / codexN.
# Sourced from ~/.zshrc (via the managed crotate block).
#
#   claudeN → plain Claude Code session on account N (acct 1 = ~/.claude,
#             acct N = ~/.claude-acctN)
#   codexN  → plain Codex session on account N (same scheme via CODEX_HOME)
#
# claude1..claude10 / codex1..codex10 are ALWAYS defined (10 account slots per
# engine by default). An account that was never ADDED (not authenticated) fails
# cleanly — e.g. `claude10` → "no claude account 10 added — authenticate it
# first: crotate login 10". Accounts ≥11 appear dynamically for every profile
# dir that exists.
#
# All args pass through to the engine, e.g. `claude3 -r` resumes on account 3.
# Glob qualifiers: (N)=nullglob, (n)=numeric sort, (/)=dirs only — the same
# discovery crotate itself uses.

for _crotn in {1..10}; do
  functions[claude${_crotn}]="crotate run ${_crotn} \"\$@\""
  functions[codex${_crotn}]="crotate run --engine codex ${_crotn} \"\$@\""
done

for _crotprof in "$HOME"/.claude-acct<11->(Nn/); do
  functions[claude${_crotprof##*-acct}]="crotate run ${_crotprof##*-acct} \"\$@\""
done
for _crotprof in "$HOME"/.codex-acct<11->(Nn/); do
  functions[codex${_crotprof##*-acct}]="crotate run --engine codex ${_crotprof##*-acct} \"\$@\""
done

unset _crotn _crotprof
