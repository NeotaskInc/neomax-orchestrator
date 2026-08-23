Rotate THIS Codex session's account in place (no re-login) and arm hands-off auto-rotation.

The user wants this Codex session to keep working past its usage limit without being at the keyboard. The engine is pure Python (`crotate`, on PATH); it swaps this CODEX_HOME's auth.json with another Codex account's so the two accounts trade places (no duplicates), and cools the spent account until its window resets.

Arguments (optional) may name a specific account to rotate to (e.g. `2` or `acct3`).

Do exactly this, then stop:

1. Run this one command with any arguments the user gave, and read its output:

   crotate session-rotate --engine codex --arm $ARGUMENTS

   - No account named: swaps with the best available Codex account (lowest 5h usage, not paused/cooled/weekly-maxed), or says you're already on the best one.
   - Account named: swaps places with that account right now.
   - --arm keeps it hands-off: the crotate launchd tick re-checks every ~60s and rotates this profile when it crosses the threshold (default 97% 5h / 99% weekly), even if this session is already rate-limited.
   - The session adopts the new account at its next token refresh — work continues.

2. Confirm back in 1-2 lines: which account this profile now holds, and that auto-rotation is armed so the user can walk away. If no other account was available (all cooled/paused/maxed), say so.

Do NOT do anything else. One command + a short confirmation.
