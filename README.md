# Claude Reasonix Fleet

This setup lets Claude Code remain the main agent while subagent-like work runs through Reasonix Fleet. The default mode is safe: it does not change Claude Code's selected main model or set a process-wide LLM gateway.

## Commands

```bash
claude-reasonix on          # enable fleet mode with the current worker count
claude-reasonix on 20       # enable fleet mode with default concurrency 20
claude-reasonix off         # disable fleet mode
claude-reasonix status      # show mode and worker count
claude-reasonix workers 20  # change default concurrent Reasonix tasks
claude-reasonix task "..."  # run one Claude task through Reasonix fleet, then auto-disable
claude-reasonix run         # start Claude in Reasonix Fleet mode
claude-reasonix router      # start explicit Claude Code Router native-subagent mode
claude-reasonix router-login # experimental: try Claude login passthrough for main Opus
claude-reasonix router-qwen # start router mode with local Qwen3.6 as the main model
claude-reasonix             # same as run: always Reasonix Fleet mode
claude-reasonix plain       # start raw Claude without Reasonix Fleet
claude-reasonix doctor      # validate files and local commands
```

`claude-reasonix` is a symlink to `claude-codex`; both names invoke the same launcher in reasonix flavor mode.

When `claude-reasonix` starts in default safe mode, it generates:

- `runtime/mcp.json` with one MCP server named `codex_fleet` for fleet dispatch and explicit batch work.

Claude keeps its normal tools, skills, plugins, workflow tools, auth path, and selected model such as `claude-opus-4-8`. Generic Claude subagents are blocked by hook policy and replaced by Reasonix Fleet MCP workers.

The experimental native gateway mode is opt-in:

```bash
CLAUDE_CODEX_NATIVE_SUBAGENTS=1 claude-reasonix
```

That mode additionally generates `runtime/agents.json`, starts a local Anthropic Messages-compatible gateway, and advertises `claude-reasonix-flash` only to that process. Use it only when you explicitly want the image-like native model column, because `ANTHROPIC_BASE_URL` is process-wide and can affect the selected main Claude model.

The preferred native-subagent experiment is Claude Code Router mode:

```bash
export ANTHROPIC_API_KEY=...   # or CLAUDE_CODEX_ANTHROPIC_API_KEY=...
claude-reasonix router
```

Router mode is explicit. Normal `claude-reasonix` does not enable it. The launcher creates a scoped CCR home under `runtime/router-sessions/<pid>/ccr-home`, starts the local alias gateway (`codex-native-gateway.py`), starts CCR on a free local port, starts a tiny Claude Code compatibility proxy in front of CCR, runs Claude with generated `codex-*`, `deepseek-*`, and `qwen-*` agents, then stops all local services when Claude exits. The proxy answers `/v1/models` with `claude-opus-4-8` and `claude-reasonix-flash` because CCR itself does not currently expose the Anthropic gateway model-discovery endpoint Claude Code expects. The generated subagent prompts begin with `<CCR-SUBAGENT-MODEL>codex-gateway,claude-reasonix-flash`, so CCR routes all worker lanes (including `codex-*` and `deepseek-*` named agents) through the local Reasonix gateway to `claude-reasonix-flash`. Ordinary main-agent Claude requests are routed by CCR back to the selected Anthropic model such as `claude-opus-4-8`, unless you use `claude-reasonix router-qwen`.

Router mode also sets `CLAUDE_CODE_SUBAGENT_MODEL=claude-reasonix-flash` by default. This is the important setting for built-in Claude Code agent teams such as `/deep-research`: phases like Scope/Search/Fetch/Verify/Synthesize inherit the Reasonix-backed model alias instead of the main Opus model. To override this, launch with `CLAUDE_CODEX_SUBAGENT_MODEL=inherit claude-reasonix router` or set it to another advertised model alias.

`claude-reasonix-flash` is backed by the Reasonix CLI (`reasonix acp`), which runs DeepSeek v4-flash locally through the Reasonix authentication layer — no OpenAI API key or DeepSeek HTTP API key is needed.

Because CCR becomes the process-local Anthropic gateway in router mode, it needs an Anthropic API key or token to preserve the main Claude model route. Without one, default safe mode and raw `claude` are unaffected, but router mode can fail when the main model request reaches the Anthropic provider.

`claude-reasonix router-login` is an experiment for Claude subscription login passthrough. In this mode, the launcher does not set `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY` for Claude. The compatibility proxy routes main Claude models such as `claude-opus-4-8` directly to the Anthropic Messages API using whatever auth header Claude Code sends from its existing login, while routing `claude-reasonix-flash` subagent aliases through CCR. This only works if Claude Code forwards login credentials to a custom `ANTHROPIC_BASE_URL`; if Claude Code withholds those credentials for custom gateways, the main model request will still fail and API-key router mode is required.

`claude-reasonix router-qwen` avoids the Anthropic main-model route entirely. It starts or verifies the local `qwen36` stack, generates a `qwen36-local` CCR provider pointing at `http://127.0.0.1:14000/v1/messages`, selects `--model qwen36-mlx`, and still exposes the Reasonix worker lanes. Use this when you want Claude Code Router behavior without Anthropic API billing or when the selected Claude model fails with "model may not exist or access".

The fleet server exposes tools for one Reasonix worker and a dynamic batch. Claude decides how many task objects to dispatch. Each task runs through `reasonix acp` and exits when done; no pool of long-lived servers is started.

## UltraCode / Dynamic Workflow

`claude-reasonix` does not auto-enable UltraCode. It passes `"ultracode": false` through `--settings` so the session starts in normal Claude Code mode, while keeping `"workflowKeywordTriggerEnabled": true` so typing `ultracode` in the prompt can still activate Dynamic Workflow.

This means `claude-reasonix` prepares the Reasonix native-agent backend, but UltraCode runs only when you ask for it with the `ultracode` keyword, run a Workflow command/tool, or use a skill that requires UltraCode/Dynamic Workflow. Dynamic Workflow remains the real Claude Code Workflow runtime when activated.

The Workflow integration is enforced at the tool boundary. In default safe mode, a `PreToolUse` hook for `Workflow` rewrites inline or saved workflow scripts before execution so each `agent(...)` lane dispatches through Reasonix Fleet MCP. The Workflow dashboard, phases, resume behavior, and script execution are still Claude Code Workflow.

In Claude Code Router mode and in the older experimental native gateway mode, the same hook instead sets `agentType` to one of the native `codex-*` or `deepseek-*` agents (all backed by `claude-reasonix-flash`). The Workflow token/tool counters and model column can then look closer to the screenshot.

Claude Code does not expose a public setting to replace the private backend implementation of Workflow's `agent()` primitive globally in safe mode; this launcher scopes the rewrite to `claude-reasonix`. For built-in Deep Research or any agent team that does not expose a script-level `agent(...)` call to the hook, use Router/native mode so `CLAUDE_CODE_SUBAGENT_MODEL` forces those internal subagents to the Reasonix alias.

## Defaults

```bash
CODEX_FLEET_MODEL=gpt-5.4
CODEX_FLEET_REASONING=xhigh
CODEX_FLEET_SERVICE_TIER=fast
CODEX_FLEET_WEB_SEARCH=live
CODEX_FLEET_SANDBOX=workspace-write
CODEX_FLEET_APPROVAL=never
CLAUDE_CODEX_FLEET_DEFAULT_WORKERS=16
CLAUDE_CODEX_NATIVE_SUBAGENTS=0
CLAUDE_CODEX_NATIVE_SUBAGENTS=1  # experimental native gateway mode
CCR_BIN=ccr
CLAUDE_CODEX_CCR_PORT=3456       # optional; router mode chooses a free port by default
CLAUDE_CODEX_ROUTER_MAIN_MODEL=claude-opus-4-8
CLAUDE_CODEX_CCR_CODEX_MODEL=claude-reasonix-flash
CLAUDE_CODEX_CCR_DEEPSEEK_MODEL=claude-reasonix-flash
CLAUDE_CODEX_CCR_QWEN_MODEL=qwen36-mlx
CLAUDE_CODEX_QWEN_ANTHROPIC_BASE_URL=http://127.0.0.1:14000/v1/messages
```

No external API key is needed for worker lanes. The gateway spawns `reasonix acp` using the Reasonix CLI login already present in your terminal session.

Normal `claude` does not read `runtime/agents.json`, does not start the gateway, and does not receive these native subagent definitions. `claude-reasonix plain` also bypasses the setup.

There is no hard task count cap in the legacy MCP launcher. `claude-reasonix workers 200` sets the default batch concurrency to 200; Claude can still send a batch with any number of tasks. In native Workflow mode, Claude Code's own Workflow runtime controls its documented agent caps and concurrency.
