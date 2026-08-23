# Neomax Orchestrator

[![CI](https://github.com/NeotaskInc/neomax-orchestrator/actions/workflows/test.yml/badge.svg)](https://github.com/NeotaskInc/neomax-orchestrator/actions/workflows/test.yml)

Neomax is Neotask's open-source universal coding-agent orchestration layer. It combines six harness surfaces—Neomax itself plus Claude Code, Codex, OpenCode, Kimi Code, and Grok Build—so one project can use multiple providers, accounts, agent harnesses, and locally available models at the same time. Any supported provider CLI can be the main orchestrator. Routing can follow an explicit user selection, an orchestrator's task-by-task decision, a plan's per-part engine and model, or dynamic provider/account eligibility; every run records the effective route and model.

Issues and pull requests are welcome at the [Neotask Inc. repository](https://github.com/NeotaskInc/neomax-orchestrator). See [CONTRIBUTING.md](CONTRIBUTING.md) and the provider-neutral [AGENTS.md](AGENTS.md) development guide.

## Multi-harness orchestration and model routing

Neomax is not merely an account rotator. It is a provider-neutral control plane for composing available coding agents into one durable fleet:

- **Six harness surfaces, one system:** use the universal `neomax` launcher or pin Claude with `cmax`, Codex with `cdxmax`, OpenCode with `ocmax`, Kimi with `kmax`, or Grok with `gmax`.
- **Providers and models can run together:** a single orchestration plan can dispatch concurrent Claude, Codex, OpenCode, Kimi, and Grok workers, with different models selected per worker or plan part.
- **Selectable and dynamic Neotask routing:** keep provider defaults, explicitly pin the orchestrator or any worker, assign an engine and model per plan part, let the orchestrator choose by task, or let Neomax dynamically select an eligible provider and account. Any model supported by the selected provider's local CLI or model registry remains available, and the effective choice is preserved in run history and usage telemetry.
- **Any connected subset works:** Neomax dynamically adapts whether the machine has one provider, several providers, or all five. No provider is required merely because another provider is orchestrating.
- **Durable execution across harnesses:** work is isolated in resumable worktrees and tracked through one lifecycle, history, usage, issue, queue, rotation, and portal surface regardless of which provider performs it.

## Automatic quota survival

Keeping that multi-provider fleet alive across accounts and usage windows is a major part of the system, not its entire purpose.

- **Usage-aware account selection:** automatic dispatch chooses an eligible account with usable quota headroom, balances live contention across the fleet, and favors soon-resetting allowance when accounts are otherwise close so expiring capacity is not wasted.
- **A hard 99% wall without stranded work:** when a provider exposes quota percentages, a profile at 99% is ineligible for automatic new work. If a delegated task starts below the wall—for example, at 92%—and reaches the limit while it is running, Neomax does not discard or restart the task from scratch. When the CLI reports the usage-limit event, Neomax cools that account and automatically continues the same durable task in the same worktree on another same-provider account. If that provider pool is exhausted, it continues cross-provider when the launch scope permits. Claude interactive sessions go further: the model-free rotation path can swap their authentication in place so the live session itself keeps going. This is continuation, not blind dispatch into an exhausted window.
- **Automatic worker failover:** a worker that hits a usage limit records the reset/cooldown and continues on another authenticated account. If that engine's account pool is exhausted, the same task continues on another allowed engine while keeping its branch, worktree, and partial progress.
- **Universal provider rotation:** `/rotate` and `neomax rotate` detect the current provider. Claude swaps accounts in place; Codex, OpenCode, Kimi, and Grok start a clean same-provider handoff on another authenticated profile while preserving worker scope and model selections. There are no separate `cdxrotate`, `krotate`, or `ocrotate` commands.
- **Automatic usage-limit management:** interactive orchestrators prefer accounts with headroom and hand off at the provider's known wall. The background usage watcher and model-free Claude rotation path can recover sessions that reach the wall between turns.
- **No invented quota data:** Claude uses its five-hour and weekly windows; Codex uses its current weekly window. OpenCode, Kimi, and Grok rotate reactively from structured rate-limit/reset evidence because their CLIs do not expose a documented percentage window.

If every allowed account and engine is exhausted, Neomax refuses a new automatic start instead of deliberately calling a known-exhausted profile. Explicit numbered-account selection remains a manual operator choice.

The harness is project-agnostic. The directory where an orchestrator starts becomes its project root, with no built-in repository names or developer paths.

## Start here: `neomax`

For normal use, run Neomax from the project directory:

```bash
cd /path/to/your/project
neomax
```

`neomax` is the universal launcher. It discovers which provider profiles are actually authenticated and eligible, excludes paused profiles, cooldowns, and known 99%-used quota windows, selects an orchestrator, and enables every viable connected provider as a worker pool. A user with only Kimi connected gets a Kimi orchestrator and Kimi workers; a user with only OpenCode and Kimi gets one eligible orchestrator plus both worker pools; a Claude/Codex installation uses those two pools without requiring configuration.

The automatic orchestrator policy is deterministic:

1. An explicit `--engine` always wins.
2. For providers with comparable numeric quota telemetry, Neomax prefers the healthy provider with the most measured headroom.
3. Providers that do not publish percentage windows remain fully eligible; Neomax ranks them by cooldown evidence, live load, and recent project selection without inventing quota percentages.
4. `--prefer` or `NEOMAX_ENGINE_PRIORITY` controls the final tie-break order.
5. `neomax launch resume` reuses the provider previously selected for that project when it remains eligible.

Useful forms:

```bash
neomax "Implement the settings page"          # dynamic orchestrator; every viable worker pool
neomax select                                  # explain the choice without launching
neomax select --json                           # machine-readable selection
neomax --engine kimi                           # pin only the orchestrator; viable pools remain mixed
neomax --prefer codex,claude,kimi,grok,opencode
neomax --workers claude,codex                   # restrict workers to an eligible subset
neomax launch resume [SESSION_ID]
neomax status
neomax portal                                  # universal five-engine portal
```

The provider-pinned launchers remain first-class: use `cmax`, `cdxmax`, `ocmax`, `kmax`, or `gmax` when a specific CLI must be the orchestrator. Their default worker scopes remain provider-specific; pass `--workers all` or a subset to change them.

## OpenCode-only quick start

Yes: the command is lowercase, one word, **`ocmax`**.

```bash
cd /path/to/the/project-you-want-to-work-on
ocmax
```

Plain `ocmax` launches OpenCode as the main orchestrator and restricts delegated workers to OpenCode. Its explicit default is:

```text
opencode-go/ox-alpha-free
```

That default covers the orchestrator, Neomax workers, OpenCode's small/system agent, and native OpenCode subagents. It never falls back silently. An explicit `--model provider/model` override repins all of those surfaces together to any model exposed by the selected profile's local OpenCode registry.

Useful OpenCode-only forms:

```bash
ocmax                         # OpenCode orchestrator; OpenCode workers only
ocmax "Finish the API work"   # start with an initial task
ocmax 2                       # pin the orchestrator to OpenCode account 2
ocmax resume                  # resume the most recent OpenCode session
ocmax resume SESSION_ID       # resume a specific OpenCode session
ocmax status                  # unified accounts and runs status
ocmax portal                  # universal five-engine portal
ocx run 1                     # plain OX session, not a Neomax orchestrator
ocx models 1                  # list account 1's local model registry
ocmax --model opencode/big-pickle
```

To let an OpenCode orchestrator delegate to every installed engine, opt in explicitly:

```bash
ocmax --workers all
```

`opencode` by itself is the upstream CLI. `ocx run` is an isolated OpenCode session with the same explicit default/override policy. Only `ocmax` activates the Neomax orchestrator prompt and cross-account delegation.

## Command map

| Command | Purpose |
|---|---|
| `neomax` | Smart universal launcher plus worker lifecycle CLI; dynamically select an eligible orchestrator and use every viable provider pool |
| `ocmax` | OpenCode orchestrator; OpenCode-only workers by default |
| `ocx` | Log in to or run an isolated OpenCode account without orchestration |
| `kmax` | Kimi K3 orchestrator; Kimi-only workers by default |
| `kmx` | Log in to or run an isolated Kimi account without orchestration |
| `gmax` | Grok Build orchestrator; Grok-only workers by default |
| `gmx` | Log in to or run an isolated Grok account without orchestration |
| `cmax` | Claude orchestrator; all worker engines by default |
| `cdxmax` | Codex orchestrator; Codex-only workers by default |
| `cdx` | Log in to or run an isolated Codex account without orchestration |
| `neomax-portal` | Run the localhost accounts/runs dashboard |
| `neomax-worktrees` | Create coordinated worktrees across a multi-repo project |
| `neomax-usage-agent` | Install or manage the background usage collector |

The namespace is intentional: `cmax` means the Claude-pinned orchestrator only. Every shared
surface uses Neomax naming—`neomax`, `neomax-portal`, `neomax-worktrees`,
`neomax-usage-agent`, `~/.neomax`, and `NEOMAX_*`. Provider launchers may expose convenience
aliases such as `cmax portal`, but they all open the same `neomax-portal` executable and the
same five-provider data model.

## Native workflows in every provider

Running `bash install.sh` installs the same four interactive workflows for every supported orchestrator:

| Workflow | Purpose |
|---|---|
| `/neomax` | Plan, dispatch, recover, verify, and integrate Neomax worker runs |
| `/rotate` | Rotate within the currently running provider's authenticated account pool |
| `/find-issues` | Find verified defects and file deduplicated project issues |
| `/fix-issues` | Claim issues, dispatch fixes, verify them, and deliver review-ready PR sets |

The files are tracked in provider-native formats, not synthesized at runtime:

| Provider | Tracked assets | Installed location |
|---|---|---|
| Claude | `claude/commands/*.md` | `~/.claude/commands/` |
| Codex | `codex/prompts/*.md` | every `<CODEX_HOME>/prompts/` |
| OpenCode | `opencode/commands/*.md` | `~/.config/opencode/commands/` |
| Kimi | `kimi/skills/*/SKILL.md` | every `<KIMI_CODE_HOME>/skills/`; use `/neomax` or `/skill:neomax` |
| Grok | `grok/commands/*.md` | every `<GROK_HOME>/commands/` |

The same `neomax` command provides smart launches, `neomax delegate` worker dispatch, and durable worker lifecycle commands for every provider.

## Install

Prerequisites:

- macOS or Linux with `zsh`, Python 3, git, and Node/npm
- At least one of [Claude Code](https://claude.com/product/claude-code), [Codex](https://developers.openai.com/codex/cli/), [OpenCode](https://opencode.ai/docs/), [Kimi Code](https://github.com/MoonshotAI/kimi-code), or [Grok Build](https://docs.x.ai/build/overview) installed
- Account 1 signed in for every engine you intend to use
- GitHub CLI `gh` signed in if workers will open pull requests

Install or refresh the launchers:

```bash
git clone https://github.com/NeotaskInc/neomax-orchestrator.git ~/neomax-orchestrator
cd ~/neomax-orchestrator
bash install.sh
```

`npm start` runs the same installer. Open a new terminal afterward so `~/.local/bin` and the shell helpers are available.

Installer settings:

```bash
NEOMAX_CLAUDE_ACCOUNTS=2 bash install.sh   # initial Claude account count
NEOMAX_CODEX_ACCOUNTS=3 bash install.sh    # separate Codex account count
```

The installer is idempotent. It symlinks the CLIs into `~/.local/bin`, creates isolated profile directories, installs every provider's native workflows plus the Claude lifecycle hooks without replacing unrelated hooks, and never writes into a project directory.

If the installer has not been run yet, the OpenCode orchestrator can be launched directly:

```bash
cd /path/to/the-project-you-want-to-work-on
~/neomax-orchestrator/bin/ocmax
```

## Accounts and profile isolation

Account numbers select isolated credential/data roots. Account 1 uses each upstream CLI's normal home. Additional accounts use engine-specific profile directories.

| Engine | Account 1 | Account N |
|---|---|---|
| Claude | normal Claude profile | `~/.claude-acctN` via `CLAUDE_CONFIG_DIR` |
| Codex | `~/.codex` | `~/.codex-acctN` via `CODEX_HOME` |
| OpenCode | normal OpenCode data root | `~/.opencode-acctN` via `XDG_DATA_HOME` |
| Kimi | `~/.kimi-code` | `~/.kimi-code-acctN` via `KIMI_CODE_HOME` |
| Grok | `~/.grok` | `~/.grok-acctN` via `GROK_HOME` |

Initial login examples:

```bash
cmax 2          # then use Claude's /login once
cdx login 2
ocx login 2
kmx login 2 oauth
gmx login 2 oauth
```

Convenience shell commands are discovered dynamically: `claudeN`, `codexN`, `opencodeN`, `kimiN`, and `grokN` appear for existing numbered profiles.

## Orchestrator and worker scopes

`neomax` chooses dynamically. A provider-pinned launcher decides which CLI is the main interactive orchestrator. `--workers` decides which engines that orchestrator may dispatch through `neomax`.

| Launcher | Main orchestrator | Default workers |
|---|---|---|
| `neomax` | Best eligible connected provider | Every eligible connected provider |
| `ocmax` | OpenCode | `opencode` |
| `cmax` | Claude | `all` |
| `cdxmax` | Codex | `codex` |
| `kmax` | Kimi Code K3 | `kimi` |
| `gmax` | Grok Build | `grok` |

All orchestrator launchers accept one engine, `all`, or any comma/plus-separated subset:

```text
claude | codex | opencode | kimi | grok | all
claude,kimi | codex+grok | any other unique subset
```

Examples:

```bash
ocmax --workers opencode    # same as plain ocmax
ocmax --workers all         # OpenCode orchestrator, mixed workers
cmax --workers claude       # Claude orchestrator, Claude-only workers
cmax --workers opencode     # Claude orchestrator, OpenCode-only workers
cdxmax --workers all        # Codex orchestrator, mixed workers
kmax                       # K3 orchestrator, Kimi workers only
kmax --workers all         # K3 orchestrator, all five worker pools
gmax                       # Grok Build orchestrator, Grok workers only
gmax --workers all         # Grok Build orchestrator, all five worker pools
cmax --workers kimi         # Claude orchestrator, Kimi workers only
cdxmax --workers kimi,opencode  # Codex orchestrator, Kimi + OpenCode workers
ocmax --workers kimi,codex  # OpenCode orchestrator, Kimi + Codex workers
kmax --workers claude,codex    # Kimi orchestrator, Claude + Codex workers
```

An invalid worker scope fails before the upstream agent CLI starts.

### Model selection for every orchestrator and worker pool

All five orchestrator launchers keep explicit defaults and accept any model their local CLI supports:

```bash
ocmax --model opencode/big-pickle       # OpenCode orchestrator + OpenCode workers
cmax --model claude-sonnet-4-6          # Claude orchestrator + Claude workers
cdxmax --model gpt-5.5                   # Codex orchestrator + Codex workers
kmax --model kimi-code/kimi-for-coding   # Kimi orchestrator + Kimi workers
gmax --model another-grok-model          # Grok orchestrator + Grok workers
```

Every launcher also accepts all five engine-specific flags. The flag matching the orchestrator changes its main model; every flag sets that delegated worker pool's default for the session:

```bash
cmax --workers opencode --opencode-model opencode/big-pickle
cdxmax --workers claude,kimi --claude-model claude-sonnet-4-6 --kimi-model kimi-code/kimi-for-coding
ocmax --workers all --codex-model gpt-5.5 --grok-model another-grok-model
```

The defaults are Claude Fable 5, Codex Sol, OpenCode OX Alpha Free, Kimi K3, and Grok 4.6. Opus 5 is never selected implicitly; choose it with `cmax --model claude-opus-5[1m]`, `neomax delegate --engine claude --opus ...`, or the equivalent Claude model flag. The flags are `--claude-model`, `--codex-model`, `--opencode-model`, `--kimi-model`, and `--grok-model`. `neomax --engine ENGINE --model MODEL` pins the smart launcher's orchestrator model; `neomax delegate --engine ENGINE --model MODEL` overrides one worker. Codex keeps the `sol`, `terra`, and `luna` aliases; Kimi keeps `k3` and `k2.7`. Other explicit IDs pass directly to the selected upstream CLI for validation. OpenCode requires the qualified `provider/model` string printed by `ocx models`, matching [OpenCode's documented model-ID format](https://opencode.ai/docs/models/).

## `ocmax` — OpenCode orchestrator

```text
ocmax [ACCOUNT] [--workers SCOPE] [--model PROVIDER/MODEL] [PROMPT...]
ocmax --orchestrator [PROMPT...]
ocmax resume [SESSION_ID]
ocmax status
ocmax portal [PORT]
```

| Form | Behavior |
|---|---|
| `ocmax` | Pick the least-busy available OpenCode account; OpenCode-only workers |
| `ocmax N` | Use OpenCode account N |
| `ocmax --workers SCOPE` | Override the worker-engine scope |
| `ocmax --model PROVIDER/MODEL` | Override OX Alpha with a qualified local-registry model for this orchestrator and its OpenCode workers |
| `ocmax --ENGINE-model MODEL` | Set any delegated engine pool's session default |
| `ocmax --orchestrator` | Use the dedicated orchestrator OpenCode profile |
| `ocmax resume` | Resume the most recently updated OpenCode session across all profiles |
| `ocmax resume ID` | Resume a specific OpenCode session |
| `ocmax status` | Show unified engine/account/run state |
| `ocmax portal [PORT]` | Open the same universal portal as `cmax portal` and `cdxmax portal` |
| `ocmax "prompt"` | Start the orchestrator with an initial task |

`ocmax` validates `opencode/model-policy.json` as its safety template, then pins every OpenCode agent entry to the selected model and provider. Sharing remains disabled. It never selects an unqualified model alias.

## `ocx` — isolated OpenCode sessions

```text
ocx login N|orch [PROVIDER]
ocx N
ocx run [N] [--model PROVIDER/MODEL] [OPEN_CODE_ARGS...]
ocx models [N] [PROVIDER]
ocx status
ocx whoami [N|orch]
ocx orch [OPEN_CODE_ARGS...]
```

| Form | Behavior |
|---|---|
| `ocx login N [PROVIDER]` | Authenticate a provider for isolated OpenCode account N; defaults to `opencode-go` |
| `ocx N` | Shorthand for `ocx login N` |
| `ocx run` | Run account 1 with OX Alpha as the default |
| `ocx run N --model PROVIDER/MODEL` | Run account N with an explicit local-registry model |
| `ocx models [N] [PROVIDER]` | List locally available qualified model IDs, optionally filtered by provider |
| `ocx status` | Show configured OpenCode profiles and current load/cooldown state |
| `ocx whoami N` | Ask OpenCode to list authenticated providers for profile N |
| `ocx orch` | Authenticate the dedicated OpenCode orchestrator profile |

`ocx` does not activate Neomax orchestration unless it is invoked through `ocmax`.

## `kmax` and `kmx` — Kimi orchestration and profiles

```text
kmax [ACCOUNT] [--workers SCOPE] [--model MODEL] [PROMPT...]
kmax --orchestrator [PROMPT...]
kmax resume [SESSION_ID]
kmax status
kmax portal [PORT]

kmx login N|orch [oauth [global|mainland-cn]|api-key|choose]
kmx N
kmx run [N] [--model MODEL] [KIMI_ARGS...]
kmx models [N]
kmx status
kmx whoami [N|orch]
kmx orch
```

Plain `kmax` defaults to K3 as the interactive orchestrator and restricts delegated workers to Kimi accounts. `--model` or `--kimi-model` selects another Kimi CLI model. `kmax --workers all` opens all five pools. It bootstraps the current `neomax orient` directive in a resumable Kimi session, then opens that session in the interactive CLI. `kmax resume` searches every isolated Kimi profile.

Kimi exposes two authentication families, and `kmx` keeps the choice explicit:

| Form | Authentication |
|---|---|
| `kmx login N oauth [global\|mainland-cn]` | Kimi Code managed-service OAuth using the official RFC 8628 device-code flow; optionally pin the Kimi AI or Kimi CN region |
| `kmx login N api-key` | Open Kimi's official `/login` UI focused on a Kimi Platform API-key login; choose the region and platform model there |
| `kmx login N choose` | Open the same official selector with all Kimi Code OAuth and Kimi Platform API-key choices |
| `kmx N` | Shorthand for `kmx login N choose` |

Managed Kimi Code OAuth is the normal path for the default K3/K2.7 Neomax pool. Kimi Platform API-key profiles remain available for ordinary `kmx run` sessions; `--model` explicitly selects a model or the profile's native configuration is used. Credentials are never copied between profiles.

The Kimi defaults and aliases are:

```text
k3    -> kimi-code/k3                 (default)
k2.7  -> kimi-code/kimi-for-coding    (optional delegated-worker model)
```

The harness does not select another Kimi model implicitly; an explicit model is passed through to Kimi for validation.

## `gmax` and `gmx` — Grok Build orchestration and profiles

The xAI coding CLI is the open-source [Grok Build](https://github.com/xai-org/grok-build) CLI, whose executable is `grok`.

```text
gmax [ACCOUNT] [--workers SCOPE] [--model MODEL] [PROMPT...]
gmax --orchestrator [PROMPT...]
gmax resume [SESSION_ID]
gmax status
gmax portal [PORT]

gmx login N|orch [oauth|device|api-key|choose]
gmx N
gmx run [N] [--model MODEL] [GROK_ARGS...]
gmx models [N]
gmx status
gmx whoami [N|orch]
gmx orch
```

Plain `gmax` runs Grok Build as the interactive orchestrator and restricts delegated workers to Grok profiles. `gmax --workers all` opens all five pools. `gmax resume` finds the owning profile from persisted Grok session summaries and uses Grok's native `--resume` support.

| Form | Authentication |
|---|---|
| `gmx login N oauth` | Browser OAuth through xAI; this is Grok's normal account login |
| `gmx login N device` | OAuth device-code login for headless/remote terminals |
| `gmx login N api-key` | Securely prompt for an xAI API key and store it only in that profile's owner-readable `auth.json` |
| `gmx login N choose` | Prompt for one of the three choices above |
| `gmx N` | Shorthand for `gmx login N choose` |

Each choice pins Grok's auth preference for that profile, so an old OAuth session cannot silently override a newly selected API key and vice versa. The orchestrator and every Neomax Grok worker default to `grok-4.6`; `--model` or `--grok-model` passes another model to Grok for validation. Headless workers use `--output-format streaming-json`; the harness records terminal token/cost usage, tool calls, native subagents, session identity, errors, and reactive rate-limit cooldowns.

## `cmax` — Claude orchestrator

```text
cmax [ACCOUNT] [--workers SCOPE] [--model MODEL] [CLAUDE_ARGS...]
cmax status
cmax portal [PORT]
cmax resume [SESSION_ID]
cmax orchestrator [CLAUDE_ARGS...]
cmax solo [CLAUDE_ARGS...]
```

| Form | Behavior |
|---|---|
| `cmax` | Pick the least-busy Claude account; all worker engines allowed |
| `cmax N` | Pin Claude account N |
| `cmax --workers SCOPE` | Limit or expand delegated worker engines |
| `cmax --model MODEL` | Set the Claude orchestrator and Claude worker default |
| `cmax --ENGINE-model MODEL` | Set another delegated engine pool's default |
| `cmax status` | Claude-launcher alias for unified `neomax status` |
| `cmax portal` | Claude-launcher alias for universal `neomax portal` on port 8787 |
| `cmax portal PORT` | Open the universal portal on a chosen port |
| `cmax resume [ID]` | Resume a Claude session |
| `cmax orchestrator` / `cmax orch` | Use the dedicated Claude orchestrator profile |
| `cmax --orchestrator` | Equivalent dedicated-profile form |
| `cmax solo` | Start Claude without the Neomax orchestration role |

## `cdxmax` and `cdx` — Codex orchestration and profiles

```text
cdxmax [ACCOUNT] [--workers SCOPE] [--model MODEL] [CODEX_ARGS...]
cdxmax status
cdxmax portal [PORT]
cdxmax resume [SESSION_ID]
cdxmax --orchestrator [CODEX_ARGS...]

cdx login N|orch
cdx N
cdx run [N] [--model MODEL] [CODEX_ARGS...]
cdx status
cdx whoami [N]
cdx orch [LOGIN_ARGS...]
```

`cdxmax` launches a Codex orchestrator and defaults to Codex-only workers. `cdx` manages ordinary isolated Codex profiles; `cdx run N --model MODEL` overrides its `gpt-5.6-sol` default. Every launcher's `portal` command executes the same server; the launcher name never filters the dashboard.

## `neomax delegate` — dispatch workers

Basic syntax:

```text
neomax delegate [OPTIONS] auto|ACCOUNT "PROMPT"
```

Examples:

```bash
neomax delegate auto "Implement the settings page"
neomax delegate --engine opencode auto "Fix the parser and add tests"
neomax delegate --engine codex 2 "Review the payment migration"
neomax delegate --engine claude --pr auto "Ship the API endpoint"
neomax delegate --engine kimi auto "Implement the migration and tests"
neomax delegate --engine kimi --kimi-model k2.7 2 "Review the generated client"
neomax delegate --engine grok auto "Implement the API and tests"
neomax delegate --wait auto "Run the focused test suite and fix failures"
```

Plain terminal sessions cannot silently become cross-account orchestrators. Cross-account `auto` dispatch is enabled by `cmax`, `cdxmax`, `ocmax`, `kmax`, or `gmax`, which set the Neomax role and allowed worker scope.

### Dispatch options

| Option | Meaning |
|---|---|
| `--engine claude\|codex\|opencode\|kimi\|grok` | Select the worker engine |
| `--model MODEL` | Select any model supported by the chosen engine; OpenCode requires `provider/model` |
| `auto` | Choose the best available account for that engine |
| `N` | Pin a numbered account |
| `-u` | Enable the Claude ultracode workflow; for Codex it selects xhigh effort; not valid for OpenCode, Kimi, or Grok |
| `--opus` | Explicitly select Claude Opus 5; Claude Fable 5 remains the default otherwise |
| `--claude-model`, `--codex-model`, `--opencode-model`, `--kimi-model`, `--grok-model` | Engine-specific aliases for `--model`; Codex and Kimi retain their short aliases |
| `-e EFFORT` | Set supported Claude/Codex reasoning effort; not valid for OpenCode, Kimi, or Grok |
| `--goal TEXT` | Give the worker a durable completion objective |
| `--max-turns N` | Limit autonomous continuation turns |
| `-t MINUTES` | Wall-clock timeout |
| `-s MINUTES` | Kill/recover a worker that stops producing progress |
| `-n` | Do not retry on a different account after quota/rate limiting |
| `--brief` | Acknowledge that a deliberately short prompt is complete and suppress the thin-brief warning |
| `--plan` | Run a read-only planning scout in the real checkout; implies `--no-worktree` (Kimi prompt mode uses an ephemeral read-tool-only profile because its CLI rejects `--prompt --plan`) |
| `--pr` | Ask the worker to open a draft pull request |
| `--base REF` | Set the worktree/PR base ref |
| `--no-worktree` | Work in the current checkout; use only when isolation is unnecessary |
| `--wait`, `--foreground`, `--fg` | Stay attached until the run finishes |
| `--detach` | Return after launch; this is the default |
| `--tag TEXT` | Attach a searchable tag to the run |

Every run is recorded as `~/.neomax/runs/RUN_ID.json`, with worker output under `~/.neomax/logs/` and parsed audit events under `~/.neomax/events/`. Detached runs retain stdout/stderr, metadata, branch/worktree identity, session identity, and completion state.

### Run lifecycle

```bash
neomax ls [--hook]
neomax log RUN_ID
neomax audit [RUN_ID]
neomax resume RUN_ID ["follow-up prompt"]
neomax retry RUN_ID [auto|ACCOUNT]
neomax kill RUN_ID
neomax pr RUN_ID
neomax pr --branch BRANCH
neomax ack RUN_ID
neomax ack --all
neomax clean RUN_ID
neomax clean --done
neomax clean --force RUN_ID
```

| Command | Purpose |
|---|---|
| `ls` | List active and recent runs; `--hook` emits compact session-start context |
| `log` | Read a run's worker output |
| `audit` | Show the durable event timeline for one run or all runs |
| `resume` | Continue a stopped/unfinished worker using its recorded session |
| `retry` | Re-run on the requested or next available account |
| `kill` | Stop the worker and mark the run killed |
| `pr` | Open a draft PR for a run or branch |
| `ack` | Acknowledge completed-run inbox entries |
| `clean` | Remove safe, merged worktrees/run artifacts; refuses unmerged work unless forced |

### Fan-out plans

```bash
neomax run-all PLAN.json
```

Example:

```json
{
  "repo": "/path/to/repo",
  "base": "main",
  "integration_branch": "feature/combined",
  "parts": [
    {
      "id": "parser",
      "prompt": "Implement the parser and tests",
      "engine": "opencode",
      "area": "src/parser"
    },
    {
      "id": "docs",
      "prompt": "Document the parser API",
      "engine": "opencode",
      "area": "docs",
      "depends_on": ["parser"]
    }
  ]
}
```

Top-level fields are `repo`, `base`, and optional `integration_branch`. Each part supports `id`, `prompt`, `engine`, `model`, `area`, `depends_on`, `effort`, `ultra`, `opus`, plus legacy `codex_model` and `kimi_model` aliases. For Claude parts, `opus: true` is an explicit Opus 5 selection; it conflicts with a different `model`. The scheduler honors dependencies, dispatches independent parts concurrently, and records integration state.

Related commands:

```bash
neomax shepherd --branch BRANCH [--base BASE] [--expect SHA] [--merge]
neomax ci-sync [--project PROJECT] [--apply] [--force]
neomax premerge-check [REPO] [--base main] [--json]
neomax modes
```

### Status, discovery, and recovery

```bash
neomax status [--json]
neomax find QUERY
neomax history [--json] [N]
neomax history RUN_ID [--log]
neomax sessions [--days N] [--json]
neomax subagents [--days N] [--json]
neomax diff RUN_ID [--json] [--patch]
neomax subagent-diff AGENT_OR_SESSION_ID [--json] [--patch]
neomax orient [RUN_ID]
neomax reconcile [OPTIONS]
neomax pause N|orch|all [--engine claude|codex|opencode|kimi|grok]
neomax unpause N|orch|all [--engine claude|codex|opencode|kimi|grok]
neomax paused
```

`sessions` inspects Claude and Codex transcripts, OpenCode SQLite sessions, Kimi session state/wire logs, and Grok session summaries/JSONL. `subagents` includes Claude Agent/workflow transcripts plus OpenCode, Kimi, and Grok native child agents, with independent model, tokens, request/error counts, tool counts, timestamps, and persisted file activity. Orchestrator-dispatched work remains in the run ledger so it is not duplicated as interactive history.

Reconciliation options:

```text
--heal
--max N
--max-age-hours H
--allow-repeat
```

Without `--heal`, reconciliation is read-only. Healing attempts bounded recovery of orphaned or inconsistent runs.

### Tasks and project registry

```bash
neomax project-register --name NAME --root DIR [OPTIONS]
neomax project-unregister NAME
neomax projects

neomax task
neomax task list [--all] [--all-projects] [--project P] [--json]
neomax task add "TITLE" [--project P] [--note TEXT] [--status S]
neomax task done|start|block|drop|reopen TASK_ID...
neomax task status TASK_ID STATUS
neomax task note TASK_ID "TEXT"
neomax task link TASK_ID RUN_ID
neomax task rm TASK_ID...
```

Project registration options:

```text
--prefix PREFIX
--repos "repo-a,repo-b"
--desc TEXT
--brain FILE
--agents FILE
--orch-brain FILE
--opener FILE
--planning FILE
--force
```

`tasks` and `backlog` are aliases for `task`. Task statuses are `todo`, `doing`, `blocked`, `done`, `merged`, and `dropped`.

`cmax`, `cdxmax`, `ocmax`, `kmax`, and `gmax` set their launch directory as `NEOMAX_PROJECT_ROOT`. On the first launch from a directory, Neomax registers that root in the local `~/.neomax/projects.json` registry without editing the project. A git repository is treated as a single-repo project; a non-git parent directory discovers immediate child git repositories. Existing `CLAUDE.md`, `AGENTS.md`, and optional `docs/neomax-orchestrator/ORCHESTRATOR_OPENER.md` files remain project-owned and are used as its instructions.

For machine-specific preconfigured projects, copy `project/projects.example.json` to `project/projects.local.json` and edit the ignored local copy. Tracked code ships with an empty project registry.

### Issues and queue

```bash
neomax issue open --title TITLE [--body BODY] [--repos a,b|--all] [--severity S] [--project P]
neomax issue list [--json] [--status S] [--project P]
neomax issue show KEY [--json]
neomax issue next [--json] [--project P] [--all|--batch N]
neomax issue claim KEY
neomax issue release KEY
neomax issue set KEY --status S
neomax issue link KEY [--run RUN_ID] [--pr REPO=URL]
neomax issue comment KEY "TEXT"
neomax issue close KEY [--comment TEXT]
neomax issue reconcile [--project P]

neomax queue status [--json]
neomax queue reserve --task TASK --agents N [--batch B] [--json]
neomax queue poll (--id RESERVATION|--task TASK) [--json]
neomax queue release (--id RESERVATION|--task TASK)
neomax queue set-budget [--agents N] [--tasks N]
```

Issues are a cross-repository GitHub issue ledger. The queue is the global FIFO agent-budget governor used to cap concurrent agents and tasks.

### Account rotation and handoff

```bash
neomax solo-rotate [OPTIONS]
neomax rotate [ACCOUNT...] [--engine claude|codex|opencode|kimi|grok] [--dry-run]
neomax session-rotate [OPTIONS]  # low-level Claude in-place primitive
neomax rotate-tick [OPTIONS]
neomax handoff --check
neomax handoff [--to ACCOUNT] [--dry-run]
neomax rotate-auth DEST --from SOURCE [--engine claude|codex] [--reason TEXT] [--swap]
neomax rotate-auth --restore DEST [--engine claude|codex]
neomax rotate-auth --log
neomax orchestrators
```

Use `/rotate`, or run `neomax rotate` directly if the active model request is already blocked. The command always stays inside the currently running provider pool. An optional account selector chooses the destination; otherwise Neomax chooses the best eligible authenticated alternative.

Claude can safely exchange stored credentials underneath the live session, so its rotation is an in-place swap and `/rotate` arms continued model-free rotation. Codex, OpenCode, Kimi, and Grok keep profiles isolated, so the same command performs a clean orchestrator handoff instead of copying tokens. The handoff preserves the current `--workers` scope and all five model selections. `session-rotate` and `solo-rotate` remain Claude-only lower-level operations; `rotate-auth` supports Claude and Codex. Structured 429 responses cool the affected profile and steer subsequent work to another authenticated profile when available.

### Usage and maintenance

```bash
neomax usage [--json] [--days N|--all]
neomax usage [--json] --since 37m|2h|90s
neomax usage [--json] --minutes N|--hours N
neomax usage-watch [--once|--rebuild|--no-backfill]
neomax keepalive [--once]
neomax tidy [--dry-run] [--any] [--json]
neomax turn-hook
neomax usage-hook
```

`turn-hook` and `usage-hook` are Claude hook entrypoints installed by the harness, not normal interactive commands. `turn-hook` adds live fleet context only in Neomax orchestrator sessions and never changes or warns about a user's model choice. Claude and Codex provide local/API quota signals. OpenCode, Kimi, and Grok expose no documented account quota-window endpoint, so their selection is load-based and reactive to 429/reset evidence; the portal never invents a percentage. OpenCode usage comes from each `opencode.db`. Kimi usage comes from each profile's session index, state, and per-agent wire logs. Grok usage comes from each profile's persisted session summaries and update JSONL: input/output/reasoning/cache tokens, completions, native subagents, models, tools, errors/429s, files, and provider-reported cost when present.

Developer/internal registry commands such as `orch-register`, `orch-unregister`, `orch-heartbeat`, `orch-pick`, `orch-note`, and `orch-busy` support launcher coordination. They are not normally called by hand.

## Portal

Start the same universal dashboard through any orchestrator launcher (or directly):

```bash
neomax portal
cmax portal
cmax portal 9000
cdxmax portal
ocmax portal
ocmax portal 9000
kmax portal
kmax portal 9000
gmax portal
gmax portal 9000
neomax-portal 8787
```

The portal binds to localhost and always shows all five engines, regardless of which launcher opened it. It includes every profile, auth method/pause/cooldown state, active orchestrators and their selected models, workers and their recorded models, interactive sessions, native subagents, run history, completion inbox, and all-engine usage. Kimi and Grok account cards and the Usage/History tabs use exact local persisted records. Its APIs and `neomax status|usage|sessions|subagents --json` share the same collectors.

## Coordinated multi-repo worktrees

```text
neomax-worktrees TASK [BRANCH] [--base REF]
neomax-worktrees --list
neomax-worktrees --remove TASK
```

Examples:

```bash
neomax-worktrees oauth-refresh feature/oauth-refresh --base main
NEOMAX_REPOS="service-a service-b" neomax-worktrees web-auth
neomax-worktrees --list
neomax-worktrees --remove web-auth
```

Configuration:

```bash
NEOMAX_PROJECT_DIR=/path/to/project-root
NEOMAX_REPOS="repo-a repo-b repo-c"
```

The helper resolves the current project from the local registry, or discovers the current git root when it is not registered. Coordinated sets live under `~/.neomax/coordinated-worktrees/` by default. `--remove` refuses worktrees with uncommitted changes.

## Background usage agent

```bash
neomax-usage-agent install
neomax-usage-agent uninstall
neomax-usage-agent status
```

The agent refreshes usage/account state used by automatic selection. Interactive commands still work without the background agent; their state may simply be less fresh.

## State and environment reference

Primary local state:

```text
~/.neomax/runs/          active run records
~/.neomax/logs/          worker output streams
~/.neomax/events/        append-only run audit events
~/.neomax/projects.json  local project roots and repository sets
~/.neomax/usage/         normalized usage snapshots
~/.local/share/opencode/opencode.db  OpenCode account 1 sessions and exact usage
~/.claude-acctN/            additional Claude profiles
~/.codex-acctN/             additional Codex profiles
~/.opencode-acctN/opencode/opencode.db  additional OpenCode profile sessions/usage
~/.kimi-code/session_index.jsonl       Kimi account 1 session index
~/.kimi-code/sessions/                 Kimi account 1 state and per-agent wire logs
~/.kimi-code-acctN/                    additional isolated Kimi profiles
~/.grok/sessions/                      Grok account 1 session summaries and update JSONL
~/.grok-acctN/                         additional isolated Grok profiles
```

Important environment variables:

| Variable | Purpose |
|---|---|
| `NEOMAX_HOME` | Override the shared local state root (default `~/.neomax`) |
| `NEOMAX_ROLE` | Marks an authorized orchestrator/worker role; launchers set it |
| `NEOMAX_PROJECT_ROOT` | Launch directory used as the active project root |
| `NEOMAX_FLEET` | Allowed delegated engines (`all` or a comma/plus-separated subset) |
| `NEOMAX_ENGINE_PRIORITY` | Final smart-orchestrator tie-break order |
| `NEOMAX_PORTAL_BIN` | Override the universal portal executable path |
| `NEOMAX_CLAUDE_ACCOUNTS` | Initial Claude profile count used by the installer |
| `NEOMAX_CODEX_ACCOUNTS` | Configured Codex account count |
| `NEOMAX_PROJECT_DIR` | Multi-repo project root |
| `NEOMAX_REPOS` | Space-separated repository list for coordinated worktrees |
| `NEOMAX_BRANCH_PREFIX` | Override the coordinated-worktree branch prefix |
| `NEOMAX_WORKTREE_ROOT` | Override coordinated-worktree storage |
| `NEOMAX_DRY_RUN` | Exercise launch/dispatch construction without starting a model request |
| `NEOMAX_CLAUDE_MODEL` | Session default for Claude workers |
| `NEOMAX_CODEX_MODEL` | Session default for Codex workers |
| `NEOMAX_OPENCODE_MODEL` | Session default for OpenCode workers (`provider/model`) |
| `NEOMAX_KIMI_MODEL` | Session default for Kimi workers |
| `NEOMAX_GROK_MODEL` | Session default for Grok workers |
| `CLAUDE_CONFIG_DIR` | Active isolated Claude profile |
| `CODEX_HOME` | Active isolated Codex profile |
| `XDG_DATA_HOME` | Active isolated OpenCode data root |
| `KIMI_CODE_HOME` | Active isolated Kimi config, credentials, sessions, and logs root |
| `GROK_HOME` | Active isolated Grok config, credentials, and sessions root |

## Model and safety policy

The explicit defaults are Claude `claude-fable-5[1m]`, Codex `gpt-5.6-sol`, OpenCode `opencode-go/ox-alpha-free`, Kimi `kimi-code/k3`, and Grok `grok-4.6`. Opus 5 is opt-in only. Every provider accepts any model supported by its local CLI, every run records the effective model, and there is no silent model fallback. OpenCode overrides retain the provider allowlist, pin primary/small/native agents together, and keep sharing disabled. Engine-specific model flags on the wrong `neomax delegate --engine` and unsupported effort flags are rejected before launch; the upstream CLI validates explicit model availability.

Worker safety properties:

- Separate worktrees prevent concurrent file overwrite.
- Durable branches and commits preserve worker results.
- Timeouts and stall watchdogs recover hung workers.
- Account cooldowns prevent immediate rate-limit retry loops.
- `clean` refuses unmerged work unless `--force` is explicit.
- Reconciliation is read-only unless `--heal` is explicit.

## Uninstall

```bash
bash uninstall.sh
bash uninstall.sh --purge-profiles
```

The default uninstall removes Neomax symlinks, provider workflows, helpers, and hooks while preserving account profiles and logins. `--purge-profiles` also removes additional Claude, Codex, OpenCode, Kimi, and Grok profiles; review that scope before using it.

## Development and verification

Repository checks:

```bash
npm test
zsh -n bin/neomax bin/cmax bin/cdxmax bin/ocmax bin/ocx bin/kmax bin/kmx bin/gmax bin/gmx
bash -n install.sh uninstall.sh
bash -n project/neomax-worktrees
python3 -m py_compile lib/neomax_core.py lib/neomax/*.py tests/test_neomax.py tests/neomax_tests/*.py bin/neomax-portal
```

Dry-run OpenCode, Kimi, and Grok construction without an authenticated request:

```bash
NEOMAX_DRY_RUN=1 ocmax
NEOMAX_DRY_RUN=1 neomax delegate --engine opencode auto "dry-run only"
NEOMAX_DRY_RUN=1 kmax
NEOMAX_DRY_RUN=1 neomax delegate --engine kimi auto "dry-run only"
NEOMAX_DRY_RUN=1 gmax
NEOMAX_DRY_RUN=1 neomax delegate --engine grok auto "dry-run only"
```

Layout:

```text
bin/neomax                 smart universal orchestrator launcher
bin/cmax                   Claude orchestrator launcher
bin/cdxmax                 Codex orchestrator launcher
bin/ocmax                  OpenCode orchestrator launcher (OX default)
bin/ocx                    isolated OpenCode profile helper
bin/kmax                  Kimi orchestrator launcher (K3 default)
bin/kmx                    isolated Kimi profile helper
bin/gmax                  Grok Build orchestrator launcher
bin/gmx                   isolated Grok profile helper
lib/neomax/               responsibility-focused orchestration implementation package
lib/neomax/module_registry.py  authoritative module, load-order, responsibility, and export registry
lib/neomax_core.py         tiny compatibility entrypoint; contains no product behavior
tests/neomax_tests/        responsibility-focused hermetic regression suites
tests/neomax_tests/suite_registry.py  authoritative test ownership and execution order
tests/test_neomax.py       tiny compatibility test runner
bin/neomax-portal            localhost dashboard
claude/commands/            Claude-native Neomax/rotate/find/fix workflows
codex/prompts/              Codex-native Neomax/rotate/find/fix workflows
opencode/commands/          OpenCode-native Neomax/rotate/find/fix workflows
opencode/model-policy.json validated OpenCode safety/default template
kimi/skills/                Kimi-native Neomax/rotate/find/fix skills
grok/commands/              Grok-native Neomax/rotate/find/fix workflows
project/neomax-worktrees      executable coordinated-worktree CLI
project/projects.example.json
install.sh
uninstall.sh
```

The extensionless files under `bin/` and `project/neomax-worktrees` are intentional Unix command entrypoints. Their first-line shebang selects Python, Bash, or Zsh, and the installer exposes the same extensionless command name on `PATH`; they are not files with missing extensions.

The implementation is intentionally modular rather than a single generated core. Every domain
module and compatibility export is declared in `lib/neomax/module_registry.py`; package startup
validates unique ownership and fails immediately on a missing export. Modules are split by stable
responsibility, not an arbitrary line count. See [ARCHITECTURE.md](ARCHITECTURE.md) before changing
package boundaries. The regression suite follows the same structure under `tests/neomax_tests/`;
its registry guarantees that every test has one owner and runs in the preserved compatibility order.

Terminal use is the supported profile-isolation path. macOS uses Keychain namespacing plus isolated config/data roots; Linux uses the corresponding file-based profile roots.

## Contributing

Public issues and pull requests are enabled. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and
use [AGENTS.md](AGENTS.md) as the canonical development guide for humans and coding agents.
Security reports belong in [private vulnerability reporting](https://github.com/NeotaskInc/neomax-orchestrator/security/advisories/new), not a public issue.
