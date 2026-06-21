# Claude Codex Fleet

This setup lets Claude Code remain the main agent while subagent-like work runs through Codex Fleet. The default mode is safe: it does not change Claude Code's selected main model or set a process-wide LLM gateway.

## Commands

```bash
claude-codex on          # enable fleet mode with the current worker count
claude-codex on 20       # enable fleet mode with default concurrency 20
claude-codex off         # disable fleet mode
claude-codex status      # show mode and worker count
claude-codex workers 20  # change default concurrent Codex tasks
claude-codex task "..."  # run one Claude task through Codex fleet, then auto-disable
claude-codex run         # start Claude in Codex Fleet mode
claude-codex router      # start explicit Claude Code Router native-subagent mode
claude-codex router-login # experimental: try Claude login passthrough for main Opus
claude-codex router-qwen # start router mode with local Qwen3.6 as the main model
claude-codex             # same as run: always Codex Fleet mode
claude-codex plain       # start raw Claude without Codex Fleet
claude-codex doctor      # validate files and local commands
```

When `claude-codex` starts in default safe mode, it generates:

- `runtime/mcp.json` with one MCP server named `codex_fleet` for legacy fleet dispatch and explicit batch work.

Claude keeps its normal tools, skills, plugins, workflow tools, auth path, and selected model such as `claude-opus-4-8`. Generic Claude subagents are blocked by hook policy and replaced by Codex Fleet MCP workers.

The experimental native gateway mode is opt-in:

```bash
CLAUDE_CODEX_NATIVE_SUBAGENTS=1 claude-codex
```

That mode additionally generates `runtime/agents.json`, starts a local Anthropic Messages-compatible gateway, and advertises `claude-codex-pro` plus `claude-deepseek-pro` only to that process. Use it only when you explicitly want the image-like native model column, because `ANTHROPIC_BASE_URL` is process-wide and can affect the selected main Claude model.

The preferred native-subagent experiment is Claude Code Router mode:

```bash
export ANTHROPIC_API_KEY=...   # or CLAUDE_CODEX_ANTHROPIC_API_KEY=...
export DEEPSEEK_API_KEY=...    # for claude-deepseek-pro
claude-codex router
```

Router mode is explicit. Normal `claude-codex` does not enable it. The launcher creates a scoped CCR home under `runtime/router-sessions/<pid>/ccr-home`, starts the local alias gateway, starts CCR on a free local port, starts a tiny Claude Code compatibility proxy in front of CCR, runs Claude with generated `codex-*`, `deepseek-*`, and `qwen-*` agents, then stops all local services when Claude exits. The proxy answers `/v1/models` with `claude-opus-4-8`, `claude-codex-pro`, `claude-deepseek-pro`, and `qwen36-mlx` because CCR itself does not currently expose the Anthropic gateway model-discovery endpoint Claude Code expects. The generated subagent prompts begin with `<CCR-SUBAGENT-MODEL>...`, so CCR routes worker lanes to `claude-codex-pro`, `claude-deepseek-pro`, or local `qwen36-mlx`. Ordinary main-agent Claude requests are routed by CCR back to the selected Anthropic model such as `claude-opus-4-8`, unless you use `claude-codex router-qwen`.

Router mode also sets `CLAUDE_CODE_SUBAGENT_MODEL=claude-codex-pro` by default. This is the important setting for built-in Claude Code agent teams such as `/deep-research`: phases like Scope/Search/Fetch/Verify/Synthesize inherit the Codex-backed model alias instead of the main Opus model. To override this, launch with `CLAUDE_CODEX_SUBAGENT_MODEL=inherit claude-codex router` or set it to another advertised model alias.

`claude-codex-pro` uses Codex CLI by default, not a raw OpenAI API key. The native gateway starts `codex exec` via `CODEX_BIN`, so it uses the same Codex login/config that works in your terminal. If you explicitly want the old OpenAI-compatible API backend, launch with `CLAUDE_CODEX_CODEX_BACKEND=openai` and set `OPENAI_API_KEY`.

Because CCR becomes the process-local Anthropic gateway in router mode, it needs an Anthropic API key or token to preserve the main Claude model route. Without one, default safe mode and raw `claude` are unaffected, but router mode can fail when the main model request reaches the Anthropic provider.

`claude-codex router-login` is an experiment for Claude subscription login passthrough. In this mode, the launcher does not set `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY` for Claude. The compatibility proxy routes main Claude models such as `claude-opus-4-8` directly to the Anthropic Messages API using whatever auth header Claude Code sends from its existing login, while routing `claude-codex-pro` and `claude-deepseek-pro` subagent aliases through CCR. This only works if Claude Code forwards login credentials to a custom `ANTHROPIC_BASE_URL`; if Claude Code withholds those credentials for custom gateways, the main model request will still fail and API/provider-key router mode is required.

`claude-codex router-qwen` avoids the Anthropic main-model route entirely. It starts or verifies the local `qwen36` stack, generates a `qwen36-local` CCR provider pointing at `http://127.0.0.1:14000/v1/messages`, selects `--model qwen36-mlx`, and still exposes the Codex/DeepSeek worker lanes. Use this when you want Claude Code Router behavior without Anthropic API billing or when the selected Claude model fails with "model may not exist or access".

The fleet server exposes tools for one Codex worker and a dynamic batch. Claude decides how many task objects to dispatch. Each task runs through `codex exec` and exits when done; no pool of 200 long-lived Codex servers is started.

## UltraCode / Dynamic Workflow

`claude-codex` does not auto-enable UltraCode. It passes `"ultracode": false` through `--settings` so the session starts in normal Claude Code mode, while keeping `"workflowKeywordTriggerEnabled": true` so typing `ultracode` in the prompt can still activate Dynamic Workflow.

This means `claude-codex` prepares the Codex/DeepSeek native-agent backend, but UltraCode runs only when you ask for it with the `ultracode` keyword, run a Workflow command/tool, or use a skill that requires UltraCode/Dynamic Workflow. Dynamic Workflow remains the real Claude Code Workflow runtime when activated.

The Workflow integration is enforced at the tool boundary. In default safe mode, a `PreToolUse` hook for `Workflow` rewrites inline or saved workflow scripts before execution so each `agent(...)` lane dispatches through Codex Fleet MCP. The Workflow dashboard, phases, resume behavior, and script execution are still Claude Code Workflow.

In Claude Code Router mode and in the older experimental native gateway mode, the same hook instead sets `agentType` to one of the native `codex-*` or `deepseek-*` agents. The Workflow token/tool counters and model column can then look closer to the screenshot.

Claude Code does not expose a public setting to replace the private backend implementation of Workflow's `agent()` primitive globally in safe mode; this launcher scopes the rewrite to `claude-codex`. For built-in Deep Research or any agent team that does not expose a script-level `agent(...)` call to the hook, use Router/native mode so `CLAUDE_CODE_SUBAGENT_MODEL` forces those internal subagents to the Codex alias.

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
CLAUDE_CODEX_CODEX_BACKEND=codex-cli # default; use openai only with OPENAI_API_KEY
CCR_BIN=ccr
CLAUDE_CODEX_CCR_PORT=3456       # optional; router mode chooses a free port by default
CLAUDE_CODEX_ROUTER_MAIN_MODEL=claude-opus-4-8
CLAUDE_CODEX_CCR_CODEX_MODEL=claude-codex-pro
CLAUDE_CODEX_CCR_DEEPSEEK_MODEL=claude-deepseek-pro
CLAUDE_CODEX_CCR_QWEN_MODEL=qwen36-mlx
CLAUDE_CODEX_QWEN_ANTHROPIC_BASE_URL=http://127.0.0.1:14000/v1/messages
CLAUDE_CODEX_CODEX_MODEL=gpt-5.4
CLAUDE_CODEX_DEEPSEEK_MODEL=deepseek-chat
```

For experimental native gateway inference, set provider keys before launching:

```bash
export OPENAI_API_KEY=...
export DEEPSEEK_API_KEY=...
CLAUDE_CODEX_NATIVE_SUBAGENTS=1 claude-codex
```

Normal `claude` does not read `runtime/agents.json`, does not start the gateway, and does not receive these native subagent definitions. `claude-codex plain` also bypasses the setup.

There is no hard task count cap in the legacy MCP launcher. `claude-codex workers 200` sets the default batch concurrency to 200; Claude can still send a batch with any number of tasks. In native Workflow mode, Claude Code's own Workflow runtime controls its documented agent caps and concurrency.
