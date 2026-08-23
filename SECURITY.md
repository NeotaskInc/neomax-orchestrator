# Security policy

Please do not report suspected vulnerabilities in a public issue.

Use GitHub's private vulnerability reporting for this repository:

https://github.com/NeotaskInc/neomax-orchestrator/security/advisories/new

Include the affected version or commit, impact, reproduction steps, and any suggested mitigation.
Remove credentials, personal data, and unrelated private source from the report. Maintainers will
acknowledge the report through the private advisory and coordinate disclosure and remediation there.

Security-sensitive areas include credential isolation and rotation, command construction, worktree
cleanup, local portal access, provider event parsing, state-file permissions, and accidental secret
or personal-data exposure.
