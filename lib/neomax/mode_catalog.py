"""Provider-neutral mode and command catalog."""


def _shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


ORCH_MODES = [
    {
        "id": "neomax-auto",
        "title": "Neomax · automatic orchestrator and fleet",
        "orchestrator": "Best eligible connected provider",
        "workers": "Every viable connected provider",
        "cmd": "neomax",
        "why": "Normal entrypoint: selects from authenticated providers using quota headroom, cooldowns, load, and configured preference.",
    },
    {
        "id": "claude-all",
        "title": "Claude orchestrator · ALL accounts",
        "orchestrator": "Claude Fable 5",
        "workers": "Claude + Codex + OpenCode + Kimi + Grok",
        "cmd": "cmax",
        "why": "Pins Claude as orchestrator while retaining the full multi-provider worker fleet.",
    },
    {
        "id": "claude-claude",
        "title": "Claude orchestrator · Claude-only",
        "orchestrator": "Claude Fable 5",
        "workers": "Claude only",
        "cmd": "cmax --workers claude",
        "why": "Pure Claude run; no Codex workers.",
    },
    {
        "id": "claude-codex",
        "title": "Claude orchestrator · Codex workers",
        "orchestrator": "Claude Fable 5",
        "workers": "Codex only",
        "cmd": "cmax --workers codex",
        "why": "Claude plans at the highest level; Codex does all the execution (save Claude quota / use Codex's bigger limits).",
    },
    {
        "id": "codex-codex",
        "title": "Codex orchestrator · Codex-only",
        "orchestrator": "Codex gpt-5.6-sol",
        "workers": "Codex only",
        "cmd": "cdxmax",
        "why": "All-Codex: one eligible Codex account orchestrates and the connected pool executes.",
    },
    {
        "id": "codex-all",
        "title": "Codex orchestrator · ALL accounts",
        "orchestrator": "Codex gpt-5.6-sol",
        "workers": "Claude + Codex + OpenCode + Kimi + Grok",
        "cmd": "cdxmax --workers all",
        "why": "Codex drives but can still farm to Claude when needed.",
    },
    {
        "id": "opencode-opencode",
        "title": "OpenCode orchestrator · OpenCode-only",
        "orchestrator": "OX Alpha Free",
        "workers": "OpenCode only",
        "cmd": "ocmax",
        "why": "OX Alpha is the default; --model selects another local-registry model.",
    },
    {
        "id": "opencode-all",
        "title": "OpenCode orchestrator · ALL accounts",
        "orchestrator": "OX Alpha Free",
        "workers": "Claude + Codex + OpenCode + Kimi + Grok",
        "cmd": "ocmax --workers all",
        "why": "OpenCode drives the full fleet; per-engine model overrides are supported.",
    },
    {
        "id": "kimi-kimi",
        "title": "Kimi K3 orchestrator · Kimi-only",
        "orchestrator": "Kimi K3",
        "workers": "Kimi only",
        "cmd": "kmax",
        "why": "K3 orchestrates; K3 or K2.7 workers execute on isolated Kimi accounts.",
    },
    {
        "id": "kimi-all",
        "title": "Kimi K3 orchestrator · ALL accounts",
        "orchestrator": "Kimi K3",
        "workers": "Claude + Codex + OpenCode + Kimi + Grok",
        "cmd": "kmax --workers all",
        "why": "K3 drives every configured engine pool.",
    },
    {
        "id": "grok-grok",
        "title": "Grok Build orchestrator · Grok-only",
        "orchestrator": "Grok Build",
        "workers": "Grok only",
        "cmd": "gmax",
        "why": "Grok Build orchestrates and executes on isolated xAI accounts.",
    },
    {
        "id": "grok-all",
        "title": "Grok Build orchestrator · ALL accounts",
        "orchestrator": "Grok Build",
        "workers": "Claude + Codex + OpenCode + Kimi + Grok",
        "cmd": "gmax --workers all",
        "why": "Grok Build drives every configured engine pool.",
    },
    {
        "id": "claude-orch-reserved",
        "title": "Claude DEDICATED orchestrator · reserved account",
        "orchestrator": "Claude Fable 5 (reserved orch acct)",
        "workers": "Claude + Codex + OpenCode + Kimi + Grok",
        "cmd": "cmax --orchestrator",
        "why": "Runs the interactive orchestrator on the DEDICATED orchestrator account (set up once: `cmax orchestrator` then /login), EXCLUDED from the worker pool — so that account ONLY orchestrates and the connected base accounts do the work. WITHOUT this flag (plain `cmax`), the orchestrator account, if logged in, joins the worker pool.",
    },
    {
        "id": "codex-orch-reserved",
        "title": "Codex DEDICATED orchestrator · reserved account",
        "orchestrator": "Codex gpt-5.6-sol (reserved orch acct)",
        "workers": "Codex (add --workers all)",
        "cmd": "cdxmax --orchestrator",
        "why": "Same on the Codex side (`cdx orch` to set up).",
    },
    {
        "id": "opencode-orch-reserved",
        "title": "OpenCode DEDICATED orchestrator · reserved account",
        "orchestrator": "OX Alpha Free (reserved orch acct)",
        "workers": "OpenCode (add --workers all)",
        "cmd": "ocmax --orchestrator",
        "why": "Dedicated OpenCode Go account; set up with `ocx orch`.",
    },
    {
        "id": "kimi-orch-reserved",
        "title": "Kimi DEDICATED orchestrator · reserved account",
        "orchestrator": "Kimi K3 (reserved orch acct)",
        "workers": "Kimi (add --workers all)",
        "cmd": "kmax --orchestrator",
        "why": "Dedicated Kimi Code account; set up with `kmx orch`.",
    },
    {
        "id": "grok-orch-reserved",
        "title": "Grok DEDICATED orchestrator · reserved account",
        "orchestrator": "Grok Build (reserved orch acct)",
        "workers": "Grok (add --workers all)",
        "cmd": "gmax --orchestrator",
        "why": "Dedicated xAI account; set up with `gmx orch`.",
    },
    {
        "id": "claude-solo",
        "title": "Solo · single account, no delegation",
        "orchestrator": "Claude Fable 5 (ultracode)",
        "workers": "its own Workflow + sub-agents",
        "cmd": 'cmax solo  ["initial task…"]',
        "section": "Start a solo session (one account, no neomax)",
        "why": "One Fable-5 ultracode session that drives its OWN Workflow + sub-agent fleet directly — NO neomax delegation, never opens /login. Runs on a single account whose auth auto-rotates IN PLACE at 99% 5h / 99% weekly (swaps to the freshest account) so a long hands-off run never stops. Use `/rotate [<acct>]` inside any session to rotate on demand.",
    },
]
ACCOUNT_CMDS = [
    {"what": "Fleet status (all accounts, usage, cooldowns)", "cmd": "neomax status"},
    {
        "what": "Authenticate the DEDICATED orchestrator account — Claude",
        "cmd": "cmax orchestrator   (then /login)",
    },
    {
        "what": "Authenticate the DEDICATED orchestrator account — Codex",
        "cmd": "cdx orch",
    },
    {
        "what": "Authenticate the DEDICATED orchestrator account — OpenCode",
        "cmd": "ocx orch",
    },
    {
        "what": "Authenticate the DEDICATED orchestrator account — Kimi",
        "cmd": "kmx orch",
    },
    {
        "what": "Authenticate the DEDICATED orchestrator account — Grok",
        "cmd": "gmx orch",
    },
    {
        "what": "Open the universal dashboard (same portal from any orchestrator)",
        "cmd": "neomax portal  |  cmax portal  |  cdxmax portal  |  ocmax portal  |  kmax portal  |  gmax portal",
    },
    {"what": "Log in / re-login Claude account N", "cmd": "cmax N   (then /login)"},
    {"what": "Log in / re-login Codex account N", "cmd": "cdx login N   (or: cdx N)"},
    {
        "what": "Log in / re-login an OpenCode provider on account N",
        "cmd": "ocx login N [provider]",
    },
    {
        "what": "List a profile's local OpenCode model registry",
        "cmd": "ocx models [N] [provider]",
    },
    {
        "what": "Log in / re-login Kimi account N",
        "cmd": "kmx login N oauth|api-key|choose",
    },
    {
        "what": "Log in / re-login Grok account N",
        "cmd": "gmx login N oauth|device|api-key",
    },
    {"what": "Codex account status", "cmd": "cdx status"},
    {"what": "OpenCode account status", "cmd": "ocx status"},
    {"what": "Kimi account status", "cmd": "kmx status"},
    {"what": "Grok account status", "cmd": "gmx status"},
    {
        "what": "Resume a quit orchestrator session (auto-finds the owning account; runs the latest code)",
        "cmd": "cmax resume [<session-id>]  |  cdxmax resume [<id>]  |  ocmax resume [<id>]  |  kmax resume [<id>]  |  gmax resume [<id>]",
    },
    {"what": "Recover in-flight / inbox runs", "cmd": "neomax ls"},
    {"what": "Permanent run history (+ logs)", "cmd": "neomax history [--log <id>]"},
    {
        "what": "All-engine token/request/error/tool usage (by account/model/provider/date)",
        "cmd": "neomax usage [--days N | --all]",
    },
    {
        "what": "Interactive session history (all five engines, by session)",
        "cmd": "neomax sessions",
    },
    {
        "what": "Sub-agent history (Claude, OpenCode, Kimi, and Grok native child sessions)",
        "cmd": "neomax subagents  ·  neomax subagent-diff <claude-id>",
    },
    {
        "what": "Exact diff of an orchestrator run (branch vs base)",
        "cmd": "neomax diff <run-id> [--patch]",
    },
    {
        "what": "Manage a run — pause(=lossless)/resume/retry",
        "cmd": "neomax kill | resume | retry <run-id>",
    },
    {
        "what": "Rotate the CURRENT provider to another authenticated same-provider account. Claude swaps in place and can stay armed; Codex, OpenCode, Kimi, and Grok perform a clean orchestrator handoff. Works without a model request when run directly.",
        "cmd": "neomax rotate [<acct>...] [--dry-run]   ·   /rotate [<acct>...]",
    },
    {
        "what": "Pause / unpause an account — hold it OUT of (or back INTO) dispatch, live, no restart",
        "cmd": "neomax pause <N|orch|all>   ·   neomax unpause <N|orch|all>",
    },
    {
        "what": 'NARROW THE ENGINE MID-SESSION (e.g. "only use Codex from now on") — the --workers scope is fixed at LAUNCH, but pausing the whole other engine is live and reversible; per-dispatch, just pass --engine codex. Widening back to an engine the launch scope excluded needs a relaunch in that mode.',
        "cmd": "neomax pause all --engine claude   ·   unpause all --engine claude",
    },
    {
        "what": "Codex model tier for a worker — sol = flagship (default), terra = balanced, luna = fast/cheap high-throughput",
        "cmd": 'neomax delegate --engine codex --codex-model sol|terra|luna auto "..."',
    },
    {
        "what": "Kimi model for a worker — K3 is default/main; K2.7 is opt-in",
        "cmd": 'neomax delegate --engine kimi --kimi-model k3|k2.7 auto "..."',
    },
    {
        "what": "Any engine's model — defaults stay pinned unless explicitly overridden",
        "cmd": 'neomax delegate --engine ENGINE --model MODEL auto "..."  ·  *max --ENGINE-model MODEL',
    },
    {
        "what": "Rotate/SWAP an account's auth IN PLACE (--swap = trade places, no duplicate accounts)",
        "cmd": "neomax rotate-auth <acct|orch> --from <acct> [--swap] [--engine codex]",
    },
    {
        "what": "Other live orchestrators (coordinate so you don't overwrite each other) + pre-merge check",
        "cmd": "neomax orchestrators   ·   neomax premerge-check",
    },
    {
        "what": "Projects — launcher cwd auto-registers; new projects can initialize their own instructions",
        "cmd": "neomax projects  ·  /project <name>  ·  neomax project-register --name <n> --root <path>",
    },
    {
        "what": "Full command + flag registry (every worker capability)",
        "cmd": "neomax help",
    },
    {
        "what": "Orchestrator rotation status / lower-level same-provider handoff",
        "cmd": "neomax handoff --check  |  neomax handoff [--to <acct>]",
    },
    {"what": "Sweep / heal stuck runs", "cmd": "neomax reconcile [--heal]"},
]
