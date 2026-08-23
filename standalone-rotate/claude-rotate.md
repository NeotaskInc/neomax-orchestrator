---
description: Arm hands-off auto-rotation — this session swaps Claude accounts at 97% 5h (or to an account you name), no /login
---

You are in ANY Claude session. The user wants this session to keep working past its 5-hour usage limit WITHOUT being at the keyboard to click through an OAuth/login.

> **If the model is ALREADY rate-limited** (this slash command won't run because the session is blocked): the engine is pure Python and needs no AI — run **`crotate session-rotate`** directly in a terminal, or type **`!crotate session-rotate`** in the Claude Code prompt box. It rotates with zero model tokens and works even when the wall is hit. (The always-on crotate launchd tick also rotates a blocked, armed session on its own within ~60s.)

`$ARGUMENTS` may name a SPECIFIC account to rotate to (e.g. `/rotate 2`, `/rotate acct3`, `/rotate 3 1`). With no account, it targets the **best available** account — **5h-first**: among usable accounts (logged-in, not paused / cooled / weekly-maxed, with 5h headroom) it picks the one with the **lowest 5-hour usage**, with a small nudge toward whichever account's **weekly resets soonest** (burn use-it-or-lose-it quota) and an anti-herd spread so concurrent rotations don't all land on the same account. Naming an account forces a deliberate "switch NOW to this one." It rotates even if the current account is below threshold.

Do exactly this, then stop:

1. Run this one command and read its output:
   ```
   crotate session-rotate --arm $ARGUMENTS
   ```
   - **No account named:** it swaps places with the best available account right now. If this account is already the best target it says so and stays put — auto-rotation stays armed regardless.
   - **Account named** (`2`, `acct3`, `3 1`, …): it **swaps places with that account right now** (first usable one if you list several). The named account takes over this session; the account you were on moves into the named account's profile slot.
   - Either way it's a **swap, not a one-way copy** — no duplicate accounts across profiles. **No /login, no browser, no OAuth click.**
   - `--arm` keeps auto-rotation on hands-off: at every turn-end (Stop hook) this session re-checks and rotates whenever the account hits the threshold; the launchd tick covers a session that's already walled. A spent account is cooled only until its window **resets**. A leftover marker is scoped to this session, so it never rotates an unrelated session later.
   - The live session adopts the swapped account at the next token refresh — work just continues. (Pass `--threshold N` to change the %, e.g. `/rotate --threshold 90`, or combine: `/rotate 3 --threshold 90`.)

2. Confirm back in 1-2 lines: which account you're on now, that auto-rotation is ARMED at the threshold, and that the user can walk away — it'll rotate hands-off when needed. If the command said the requested account can't be used or that no other account is available (all cooled/paused/maxed), say so and what happens next.

Do NOT do anything else. This is a quick, deterministic action — one command + a short confirmation.
