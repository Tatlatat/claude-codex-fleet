# claude-reasonix — DeepSeek-via-Reasonix-CLI native subagent

**Date:** 2026-06-20
**Status:** Design approved, ready for implementation plan
**Owner:** Hưng (hungdotmn@gmail.com)

## Summary

Add a second launcher mode, `claude-reasonix`, symmetric to the existing
`claude-codex`. It exposes a single native subagent — **`claude-reasonix-flash`**
— backed by **DeepSeek run through the Reasonix CLI** (`reasonix acp`), NOT the
DeepSeek HTTP API. The two modes share the entire codex-fleet infrastructure
(gateway, ccr-proxy, `/rc`, UltraCode workflow lanes, self-heal, the
heartbeat-streaming fixes); they differ only in which provider/agent each one
exposes. `claude-codex` keeps exposing only Codex agents; `claude-reasonix`
exposes only Reasonix agents.

## Goals

- A `claude-reasonix` mode usable exactly like `claude-codex` (incl.
  `router-login --dangerously-skip-permissions`, `/rc`, UltraCode).
- Reasonix lanes **write real files** in the workspace (like codex lanes).
- **Cost minimized at the Reasonix CLI layer** (not by limiting agent count).
- **Unlimited agents:** if Claude wants 1000 lanes it gets 1000 — fan-out is the
  source of UltraCode power; savings come from cheap-per-agent, not fewer agents.
- Reasonix lanes can **search the web**.

## Non-goals

- No DeepSeek HTTP API usage (no `DEEPSEEK_API_KEY` path for this mode).
- No new gateway/proxy/`/rc` code — reuse the existing, already-patched stack.
- Not touching `claude-codex` behavior.

## Key decisions (all user-confirmed)

1. **Two separate launchers, one codebase.** `claude-reasonix` is a
   wrapper/symlink that runs the same launcher in a "reasonix flavor" (env
   vars), NOT a fork. Each mode exposes exactly one agent kind.
2. **Exposed agent name:** `claude-reasonix-flash`.
3. **Provider `reasonix_cli` calls `reasonix acp`** (Agent Client Protocol,
   JSON-RPC NDJSON over stdio) — the only Reasonix mode with real file-writing
   agent tools. Verified protocol: `initialize` → `session/new` (returns
   `sessionId`) → `session/prompt` (with sessionId) → stream of `session/update`
   notifications → `{"stopReason":"end_turn"}`. With `--yolo` it writes files for
   real (verified: created hello.txt=PONG).
4. **Cost strategy at CLI layer (verified with real cost numbers):**
   - `--no-config` — the user's `~/.reasonix/config.json` is `v4-pro` +
     `effort:max` (the most expensive combo); ignore it.
   - `-m deepseek-v4-flash` — strong enough (solved a logic puzzle, wrote correct
     `merge_intervals` and an O(1) `LRUCache`); far cheaper than pro.
   - `--budget 0.05` — hard cap per subagent. A real LRUCache task cost only
     $0.00018, so $0.05 is a wide ceiling that still stops runaways.
   - `--effort high` default; Claude raises to `max` when a lane needs it.
   - Cache is the big lever: observed 90–93% cache → cost drops to ~$0.000024/call.
5. **Web search: KEPT.** `reasonix acp` has web_search/fetch as **built-in agent
   tools** that auto-activate when a task needs them. **v4-flash uses them in acp
   mode and returns correct results** (verified: returned Godot "4.7"). No flag
   needed — unlike codex's `-c web_search="live"`. NOTE: web search works only via
   `acp`, NOT via `reasonix run` (run returns `<<<NEEDS_PRO>>>` for flash).

## Architecture

```
claude-reasonix router-login
  → same launcher, reasonix flavor (env: provider=reasonix_cli, model=v4-flash, …)
  → Claude Code (ANTHROPIC_BASE_URL = ccr-proxy)
       → ccr-claude-proxy.py  (line-streaming SSE — Fix A)
            → codex-native-gateway.py  (always-stream heartbeat)
                 → run_reasonix_acp()  ← NEW
                      → reasonix acp --dir <cwd> --yolo -m deepseek-v4-flash
                        --effort <e> --budget 0.05 --no-config [--transcript …]
```

The only new code is the `reasonix_cli` provider + `run_reasonix_acp()` in the
gateway, the launcher reasonix flavor, the workflow-hook reasonix agentTypes, and
the reasonix system-prompt. Everything else is reused.

### Component 1 — `run_reasonix_acp(prompt, config)` (gateway)

Symmetric replacement for `run_codex_cli`. Returns `(text, usage)`.

- Spawn `reasonix acp --dir <cwd> --yolo -m <model> --effort <effort>
  --budget <budget> --no-config [--transcript <path>]`.
- Speak JSON-RPC NDJSON over stdin/stdout:
  1. send `initialize`; read result.
  2. send `session/new {cwd}`; read `sessionId`.
  3. send `session/prompt {sessionId, prompt:[{type:text,text:prompt}]}`.
  4. read `session/update` notifications, accumulating `agent_message_chunk`
     text blocks, until the `session/prompt` result arrives with `stopReason`.
  5. return accumulated text + a usage object; capture Reasonix's
     `cost:$X cache:Y%` line into usage for tracking.
- Reuse codex's operational envelope: timeout 600s (`CLAUDE_CODEX_GATEWAY_*`),
  3 retries, the codex concurrency semaphore (or a reasonix-specific one),
  per-request logging to `CLAUDE_CODEX_GATEWAY_*_LOG_DIR`.
- Wrapped by the gateway's `send_sse_response_lazy` (heartbeat) like codex, so the
  180s watchdog never fires — Fix A + always-stream already handle this once the
  provider returns through the same path.

### Component 2 — Provider registry entry

A `claude-reasonix-flash` model entry with `provider: "reasonix_cli"`, plus the
gateway's `/v1/messages` codex_cli branch generalized to also take the
`reasonix_cli` branch (both go through `send_sse_response_lazy`).

### Component 3 — Launcher reasonix flavor

`claude-reasonix` (symlink/wrapper) sets, before delegating to the existing
launcher: provider=reasonix_cli, default model=deepseek-v4-flash, budget=0.05,
effort=high, exposed alias model = `claude-reasonix-flash`, and the reasonix
system-prompt. `router-login`, gateway start, ccr-proxy start, `/rc`, bypass —
all unchanged.

### Component 4 — Workflow hook reasonix agentTypes

In reasonix mode, `codex-workflow.py` rewrites `agent()` lanes to
`reasonix-worker` / `reasonix-verify` / `reasonix-research` / … (all mapping to
`claude-reasonix-flash`). Reasonix mode exposes ONLY reasonix agentTypes; codex
mode keeps exposing only codex ones.

### Component 5 — Reasonix system-prompt (orchestration guidance)

Tells Claude how to split work for v4-flash:
- Reasonix lanes are strongest on **atomic, well-scoped** tasks (one
  function / class / module / question). Decompose a large task into **many small
  lanes** rather than one big lane — agent count is unlimited.
- Each lane is capped at $0.05; v4-flash + cache is very cheap → fan out widely.
- Web search is available inside a lane (built-in tool, no flag).

## reasonix acp vs codex exec — parameter mapping

| codex exec | reasonix acp |
|---|---|
| `--sandbox workspace-write` | `--dir <cwd> --yolo` |
| `-c web_search="live"` | (built-in tool, auto — no flag) — web search KEPT |
| `service_tier="fast"`, `features.fast_mode` | dropped (not in acp) |
| `model_reasoning_effort="xhigh"` | `--effort high` (max when needed) |
| `-m gpt-5.4` | `-m deepseek-v4-flash` |
| stdin prompt → stdout text | JSON-RPC NDJSON session (init→new→prompt→updates) |
| (none) | `--budget 0.05`, `--no-config`, `--transcript` |
| timeout 600 / retry 3 / semaphore / heartbeat | unchanged |

## Error handling

- Reasonix CLI not found / not logged in → gateway returns a clear auth-style
  error (mirror codex's "needs login" message).
- acp handshake failure / no sessionId → GatewayError, retried up to 3×.
- `--budget 0.05` hit mid-lane → return whatever text accumulated + mark
  budget-capped in usage (don't crash the lane).
- Self-heal preflight: reuse existing gateway/auth probes; add a reasonix-CLI
  presence check for reasonix mode.

## Testing

- Unit: `run_reasonix_acp` against a fake `reasonix` binary that speaks the ACP
  handshake (initialize/session-new/prompt/update/stopReason) and writes a file;
  assert text accumulation, file write, usage/cost capture, retry on handshake
  failure, budget-cap handling.
- Gateway: `/v1/messages` with a `reasonix_cli` model takes the heartbeat-stream
  path (reuse the non-stream-heartbeat test shape).
- Launcher: `claude-reasonix` exposes `claude-reasonix-flash` and the reasonix
  system-prompt, and exposes NO codex agentTypes.
- Wire all into `tests/test-codex-fleet.sh`; full suite must stay green.

## Open dependency

Codex stability had to be fixed first (the heartbeat chain). DONE:
gateway always-stream + ccr-proxy line-streaming. Reasonix reuses that fixed
chain, so it inherits the 180s-watchdog fix for free.
