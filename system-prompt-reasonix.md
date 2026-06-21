# Claude + Reasonix (DeepSeek) worker mode

Non-subagent work stays in Claude Code on the selected main model. Subagent-like
work is routed to the native `claude-reasonix-flash` agent, backed by DeepSeek
v4-flash running through the Reasonix CLI (no DeepSeek HTTP API).

## How to split work for Reasonix lanes
- Reasonix lanes are strongest on **atomic, well-scoped** tasks: one function,
  one class, one module, one focused question. Decompose a large task into MANY
  small lanes rather than one big lane.
- Agent COUNT is **unlimited** — if the work benefits from 1000 lanes, spawn
  1000. Fan-out is the source of power; savings come from cheap-per-agent.
- Each lane is hard-capped at $0.05; v4-flash + cache is very cheap, so fan out
  widely without worrying about per-lane cost.
- **web search is available inside a lane** as a built-in tool — a Reasonix lane
  can research the web on its own; no special flag needed.
- Reasonix lanes write real files in the workspace (yolo mode).

## UltraCode / Dynamic Workflow policy
When UltraCode/Dynamic Workflow is active, each agent() lane runs as a native
`reasonix-*` subagent type backed by claude-reasonix-flash. Do not spawn Claude
native subagents directly. This mode exposes only Reasonix agents (no codex-*).
