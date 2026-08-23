---
description: Rotate the current Neomax session to another authenticated Claude account
---

Rotate the current Neomax session within Claude's account pool. `$ARGUMENTS` may name one or more preferred accounts, such as `2`, `acct3`, or `3 1`; with no account, Neomax chooses the best eligible alternative.

Run exactly this command:

```sh
neomax rotate --engine claude --arm $ARGUMENTS
```

Read the result and report it briefly. Claude rotation swaps authenticated accounts in place, without `/login`, and arms the session for model-free rotation at the configured usage wall. If the current model request is already blocked, the user can run the same command directly in a terminal.
