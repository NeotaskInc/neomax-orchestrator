---
description: Rotate the current Neomax orchestrator to another authenticated OpenCode account
---

Rotate the current Neomax orchestrator within OpenCode's account pool. `$ARGUMENTS` may name one or more preferred accounts, such as `2`, `acct3`, or `3 1`; with no account, Neomax chooses the best eligible alternative.

Run exactly this command:

```sh
neomax rotate --engine opencode $ARGUMENTS
```

Read the result and report it briefly. OpenCode credentials remain isolated, so Neomax performs a clean same-provider handoff to a fresh OpenCode orchestrator and preserves the worker scope and selected models. If the current model request is already blocked, the user can run the same command directly in a terminal.
