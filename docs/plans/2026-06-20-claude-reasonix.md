# claude-reasonix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `claude-reasonix` launcher mode that exposes a single native subagent `claude-reasonix-flash`, backed by DeepSeek run through the Reasonix CLI (`reasonix acp`), reusing the entire codex-fleet infrastructure.

**Architecture:** A new gateway provider `reasonix_cli` whose `run_reasonix_acp()` speaks ACP JSON-RPC NDJSON to `reasonix acp` (real file writes via `--yolo`, web search via built-in tools). A registry entry `claude-reasonix-flash`. A launcher "reasonix flavor" (env-driven, selected by the `claude-reasonix` command name). Workflow-hook reasonix agentTypes. Everything else — gateway streaming/heartbeat, ccr-proxy line-streaming, `/rc`, self-heal — is reused unchanged.

**Tech Stack:** Python 3.14 (gateway, hooks), Bash (launcher `~/.local/bin/claude-codex`), Reasonix CLI v0.53.2 (`reasonix acp`), DeepSeek v4-flash.

## Global Constraints

- Reasonix invoked ONLY via `reasonix acp` (NOT `reasonix run`; NOT DeepSeek HTTP API). Web search works only through `acp`.
- Exposed agent name is exactly `claude-reasonix-flash`.
- Default Reasonix CLI flags: `--dir <cwd> --yolo -m deepseek-v4-flash --effort high --budget 0.05 --no-config` (effort/model/budget overridable by env).
- Per-subagent hard cost cap: `--budget 0.05`. Agent COUNT is never capped.
- Reuse codex's operational envelope: timeout default 600s, 3 retries, concurrency semaphore, per-request log dir.
- Reasonix lanes must go through the gateway's `send_sse_response_lazy` heartbeat path (same as codex_cli) so the 180s watchdog never fires.
- `~/.claude/codex-fleet` is NOT a git repo → use `git add -A && git commit` only if a repo is initialized; otherwise SKIP commit steps (note in each commit step).
- Reasonix CLI path: resolved via `REASONIX_BIN` env (default `reasonix`, found on PATH).
- Run the full suite with `bash tests/test-codex-fleet.sh` after each task; it must stay exit 0.

---

## File Structure

- `codex-native-gateway.py` (modify) — add `run_reasonix_acp()`, the `reasonix_cli` registry entry `claude-reasonix-flash`, and the `reasonix_cli` dispatch branch in `call_openai_compatible`.
- `~/.local/bin/claude-codex` (modify) — detect `claude-reasonix` command name → set reasonix-flavor env before delegating to the existing router path.
- `~/.local/bin/claude-reasonix` (create) — symlink to `claude-codex`.
- `hooks/codex-workflow.py` (modify) — emit `reasonix-*` agentTypes when the session is in reasonix flavor.
- `hooks/workflow_selfheal.py` (modify) — add a reasonix-CLI presence check in reasonix flavor.
- `system-prompt-reasonix.md` (create) — orchestration guidance for v4-flash.
- `tests/test-reasonix-acp.py` (create) — unit tests for `run_reasonix_acp` with a fake reasonix binary.
- `tests/test-codex-fleet.sh` (modify) — wire in the new tests + a launcher reasonix-flavor assertion.

---

### Task 1: `run_reasonix_acp()` — ACP driver in the gateway

**Files:**
- Modify: `codex-native-gateway.py` (add function near `run_codex_cli`, ~line 887)
- Create: `tests/test-reasonix-acp.py`
- Modify: `tests/test-codex-fleet.sh` (wire in)

**Interfaces:**
- Consumes: `env_first`, `env_int`, `env_float`, `gateway_trace`, `GatewayError` (already in `codex-native-gateway.py`).
- Produces: `run_reasonix_acp(prompt: str, config: JSON) -> tuple[str, JSON]` — returns `(accumulated_text, usage_dict)`. `usage_dict` has keys `input_tokens:int`, `output_tokens:int`, and `reasonix_cost_usd:float|None`, `reasonix_cache_pct:float|None`. Raises `GatewayError(504, "reasonix_timeout", ...)` on timeout, `GatewayError(502, "reasonix_acp_error", ...)` on handshake/spawn failure.

- [ ] **Step 1: Write the failing test (fake reasonix binary speaking ACP)**

Create `tests/test-reasonix-acp.py`:

```python
#!/usr/bin/env python3
"""Unit tests for run_reasonix_acp using a fake `reasonix` binary that speaks ACP."""
from __future__ import annotations
import importlib.util, json, os, stat, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rx_gateway", ROOT / "codex-native-gateway.py")
gw = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(gw)

def expect(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")

# A fake `reasonix` that: reads NDJSON requests, answers initialize + session/new,
# streams two agent_message_chunk updates ("PO","NG"), then returns stopReason and
# prints the reasonix cost line on stderr.
FAKE = r'''#!/usr/bin/env python3
import sys, json
def w(o): sys.stdout.write(json.dumps(o)+"\n"); sys.stdout.flush()
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    m=json.loads(line)
    mid=m.get("id"); method=m.get("method")
    if method=="initialize":
        w({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":1,"agentInfo":{"name":"reasonix"}}})
    elif method=="session/new":
        w({"jsonrpc":"2.0","id":mid,"result":{"sessionId":"sess_test"}})
    elif method=="session/prompt":
        for piece in ("PO","NG"):
            w({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"sess_test","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":piece}}}})
        sys.stderr.write("— turns:1 cache:90.0% cost:$0.000123 save-vs-claude:99.0%\n"); sys.stderr.flush()
        w({"jsonrpc":"2.0","id":mid,"result":{"stopReason":"end_turn","transcriptPath":None}})
'''

def make_fake():
    d = tempfile.mkdtemp()
    p = Path(d) / "reasonix"
    p.write_text(FAKE, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)

def test_accumulates_text_and_cost():
    fake = make_fake()
    cfg = {"reasonix_bin": fake, "target_model": "deepseek-v4-flash"}
    text, usage = gw.run_reasonix_acp("say PONG", cfg)
    expect(text == "PONG", f"expected accumulated 'PONG', got {text!r}")
    expect(usage.get("reasonix_cost_usd") == 0.000123, f"cost not captured: {usage}")
    expect(usage.get("reasonix_cache_pct") == 90.0, f"cache pct not captured: {usage}")

def main():
    test_accumulates_text_and_cost()
    print("PASS: reasonix acp driver")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test-reasonix-acp.py`
Expected: FAIL — `AttributeError: module 'rx_gateway' has no attribute 'run_reasonix_acp'`

- [ ] **Step 3: Implement `run_reasonix_acp` in `codex-native-gateway.py`**

Add after `run_codex_cli` (after ~line 1001). Insert this function:

```python
def run_reasonix_acp(prompt: str, config: JSON) -> tuple[str, JSON]:
    import queue as _queue
    reasonix_bin = str(config.get("reasonix_bin") or env_first("REASONIX_BIN", default="reasonix"))
    model = str(config.get("target_model") or "deepseek-v4-flash")
    effort = env_first("CLAUDE_CODEX_REASONIX_EFFORT", default="high")
    budget = env_first("CLAUDE_CODEX_REASONIX_BUDGET", default="0.05")
    timeout = float(env_first("CLAUDE_CODEX_GATEWAY_CODEX_TIMEOUT", "CODEX_FLEET_TIMEOUT_SECONDS", default="600"))
    cwd = env_first("CLAUDE_CODEX_GATEWAY_CODEX_CWD", default=os.getcwd())
    max_attempts = max(1, env_int("CLAUDE_CODEX_GATEWAY_CODEX_MAX_ATTEMPTS", default=3))
    semaphore = codex_cli_semaphore()
    command = [
        reasonix_bin, "acp",
        "--dir", cwd,
        "--yolo",
        "-m", model,
        "--effort", effort,
        "--budget", budget,
        "--no-config",
    ]

    def _attempt() -> tuple[str, JSON]:
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, cwd=cwd,
        )
        out_q: _queue.Queue = _queue.Queue()
        text_parts: list[str] = []
        session_id = {"v": None}
        prompt_done = {"v": False}
        stop_reason = {"v": None}

        def send(obj: JSON) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                out_q.put(msg)
            out_q.put({"__eof__": True})

        threading.Thread(target=reader, daemon=True).start()
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": 1, "clientCapabilities": {}}})
        send({"jsonrpc": "2.0", "id": 2, "method": "session/new",
              "params": {"cwd": cwd, "mcpServers": []}})

        import time as _time
        deadline = None
        while True:
            try:
                msg = out_q.get(timeout=1.0)
            except Exception:
                if deadline is not None and _time.monotonic() > deadline:
                    proc.kill()
                    raise GatewayError(504, "reasonix_timeout", f"reasonix acp timed out after {timeout:g}s")
                continue
            if msg.get("__eof__"):
                break
            if msg.get("id") == 2 and "result" in msg:
                session_id["v"] = msg["result"].get("sessionId")
                if not session_id["v"]:
                    proc.kill()
                    raise GatewayError(502, "reasonix_acp_error", "session/new returned no sessionId")
                send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
                      "params": {"sessionId": session_id["v"],
                                 "prompt": [{"type": "text", "text": prompt}]}})
                deadline = _time.monotonic() + timeout
            elif msg.get("method") == "session/update":
                upd = (msg.get("params") or {}).get("update") or {}
                if upd.get("sessionUpdate") == "agent_message_chunk":
                    content = upd.get("content") or {}
                    if isinstance(content, dict) and content.get("type") == "text":
                        text_parts.append(content.get("text", ""))
            elif msg.get("id") == 3 and "result" in msg:
                stop_reason["v"] = msg["result"].get("stopReason")
                prompt_done["v"] = True
                break
            elif msg.get("id") == 3 and "error" in msg:
                proc.kill()
                raise GatewayError(502, "reasonix_acp_error", msg["error"].get("message", "session/prompt error"))

        try:
            proc.terminate()
        except Exception:
            pass
        stderr_text = ""
        try:
            stderr_text = proc.stderr.read() if proc.stderr else ""
        except Exception:
            pass
        cost = None
        cache = None
        m = re.search(r"cost:\$([0-9.]+)", stderr_text)
        if m:
            cost = float(m.group(1))
        c = re.search(r"cache:([0-9.]+)%", stderr_text)
        if c:
            cache = float(c.group(1))
        text = "".join(text_parts)
        usage = {
            "input_tokens": estimate_tokens({"messages": [{"role": "user", "content": prompt}]}),
            "output_tokens": max(1, len(text) // 4),
            "reasonix_cost_usd": cost,
            "reasonix_cache_pct": cache,
        }
        return text, usage

    with semaphore:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                gateway_trace("reasonix_acp_attempt", model=model, attempt=attempt)
                return _attempt()
            except GatewayError as exc:
                last_exc = exc
                if exc.error_type == "reasonix_timeout":
                    raise
        if last_exc:
            raise last_exc
        raise GatewayError(502, "reasonix_acp_error", "reasonix acp produced no result")
```

Confirm `import re`, `import threading`, `import subprocess`, and `estimate_tokens` already exist at module top (they do — used by `run_codex_cli`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test-reasonix-acp.py`
Expected: `PASS: reasonix acp driver`

- [ ] **Step 5: Wire test into the suite and run full regression**

Append to `tests/test-codex-fleet.sh` (after the last `python3 ... || fail` line):

```bash
python3 "$ROOT/tests/test-reasonix-acp.py" || fail "reasonix acp driver regression"
```

Run: `bash tests/test-codex-fleet.sh`
Expected: exit 0, includes `PASS: reasonix acp driver`

- [ ] **Step 6: Commit (skip if not a git repo)**

```bash
cd ~/.claude/codex-fleet && git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add codex-native-gateway.py tests/test-reasonix-acp.py tests/test-codex-fleet.sh && \
  git commit -m "feat(gateway): add run_reasonix_acp ACP driver for Reasonix CLI" || echo "not a git repo — skip commit"
```

---

### Task 2: Registry entry + dispatch branch for `reasonix_cli`

**Files:**
- Modify: `codex-native-gateway.py` — `model_registry()` (~line 88) and `call_openai_compatible` dispatch (~line 350)
- Modify: `tests/test-reasonix-acp.py` (add a registry + dispatch test)

**Interfaces:**
- Consumes: `run_reasonix_acp` (Task 1), `anthropic_messages_to_openai`, `openai_messages_to_prompt` (existing).
- Produces: registry key `claude-reasonix-flash` with `provider: "reasonix_cli"`, `target_model`, `reasonix_bin`. Dispatch: when `config["provider"] == "reasonix_cli"`, `call_openai_compatible` returns `run_reasonix_acp(prompt, config)`'s `(text, usage)` wrapped in an Anthropic end-turn response.

- [ ] **Step 1: Write the failing test**

Add to `tests/test-reasonix-acp.py` (before `main`, and call it in `main`):

```python
def test_registry_has_reasonix_flash():
    os.environ["CLAUDE_CODEX_FLAVOR"] = "reasonix"
    try:
        reg = gw.model_registry()
        expect("claude-reasonix-flash" in reg, f"registry missing claude-reasonix-flash: {list(reg)}")
        cfg = reg["claude-reasonix-flash"]
        expect(cfg.get("provider") == "reasonix_cli", f"wrong provider: {cfg}")
        expect(cfg.get("target_model") == "deepseek-v4-flash", f"wrong model: {cfg}")
    finally:
        os.environ.pop("CLAUDE_CODEX_FLAVOR", None)
```

Add `test_registry_has_reasonix_flash()` to `main()` before the print.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test-reasonix-acp.py`
Expected: FAIL — `registry missing claude-reasonix-flash`

- [ ] **Step 3: Add the registry entry**

In `model_registry()` return dict (after the `claude-deepseek-pro` entry, inside the same `return {...}`), add:

```python
        "claude-reasonix-flash": {
            "display_name": os.getenv("CLAUDE_CODEX_REASONIX_DISPLAY_NAME", "claude-reasonix-flash"),
            "provider": "reasonix_cli",
            "target_model": env_first("CLAUDE_CODEX_REASONIX_MODEL", default="deepseek-v4-flash"),
            "reasonix_bin": env_first("REASONIX_BIN", default="reasonix"),
        },
```

- [ ] **Step 4: Add the dispatch branch**

In `call_openai_compatible`, immediately AFTER the `if config.get("provider") == "codex_cli":` block closes (before the deepseek/openai HTTP path), add:

```python
    if config.get("provider") == "reasonix_cli":
        messages = anthropic_messages_to_openai(payload)
        prompt = openai_messages_to_prompt(messages, payload.get("tools"))
        text, usage = run_reasonix_acp(prompt, config)
        gateway_trace("reasonix_acp_response", model=requested_model,
                      cost=usage.get("reasonix_cost_usd"), cache=usage.get("reasonix_cache_pct"))
        return anthropic_end_turn_response(requested_model, usage, text=text)
```

(Confirm `anthropic_end_turn_response` exists — it's used by the codex_cli branch.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 tests/test-reasonix-acp.py`
Expected: `PASS: reasonix acp driver`

- [ ] **Step 6: Full regression + commit**

Run: `bash tests/test-codex-fleet.sh` → exit 0

```bash
cd ~/.claude/codex-fleet && git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add codex-native-gateway.py tests/test-reasonix-acp.py && \
  git commit -m "feat(gateway): register claude-reasonix-flash + reasonix_cli dispatch" || echo "skip commit"
```

---

### Task 3: `/v1/messages` heartbeat path covers `reasonix_cli`

**Files:**
- Modify: `codex-native-gateway.py` — `do_POST` `/v1/messages` branch (~line 1232, the `provider == "codex_cli"` heartbeat guard)
- Modify: `tests/test-gateway-nonstream-heartbeat.py` (add a reasonix case)

**Interfaces:**
- Consumes: existing `send_sse_response_lazy`, `call_openai_compatible`, `model_registry`.
- Produces: a `/v1/messages` request for a `reasonix_cli` model takes the `send_sse_response_lazy` (heartbeat) path, same as codex_cli — both for stream and non-stream requests.

- [ ] **Step 1: Write the failing test**

Add to `tests/test-gateway-nonstream-heartbeat.py` a variant that installs a fake `reasonix_cli` registry entry and asserts the non-stream `/v1/messages` response is SSE (`event: message_start` present). Reuse the existing `FakeHandler`/`install_fake_codex` shape, but make `fake_registry` return `{"claude-reasonix-flash": {"provider": "reasonix_cli"}}` and monkeypatch `gw.run_reasonix_acp` to return `("PONG", {...})`. Assert `"event: message_start" in out` and `"PONG" in out`. Add the new test to `main()`.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test-gateway-nonstream-heartbeat.py`
Expected: FAIL — reasonix non-stream returns a JSON blob, not SSE.

- [ ] **Step 3: Generalize the heartbeat guard**

In `do_POST` `/v1/messages`, change the streaming-path condition so BOTH codex_cli and reasonix_cli take `send_sse_response_lazy`:

```python
                    provider = config.get("provider")
                    if provider in ("codex_cli", "reasonix_cli"):
                        self.send_sse_response_lazy(
                            lambda: call_openai_compatible(payload, model, config),
                            model,
                        )
                    elif payload.get("stream"):
                        response = call_openai_compatible(payload, model, config)
                        self.send_sse_response(response)
                    else:
                        response = call_openai_compatible(payload, model, config)
                        self.send_json(200, response)
                    return
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 tests/test-gateway-nonstream-heartbeat.py`
Expected: `PASS: gateway non-stream codex heartbeat`

- [ ] **Step 5: Full regression + commit**

Run: `bash tests/test-codex-fleet.sh` → exit 0

```bash
cd ~/.claude/codex-fleet && git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add codex-native-gateway.py tests/test-gateway-nonstream-heartbeat.py && \
  git commit -m "feat(gateway): route reasonix_cli through heartbeat-streaming path" || echo "skip commit"
```

---

### Task 4: Launcher reasonix flavor + `claude-reasonix` symlink

**Files:**
- Modify: `~/.local/bin/claude-codex` — flavor detection by command name
- Create: `~/.local/bin/claude-reasonix` (symlink)
- Modify: `tests/test-codex-fleet.sh` (launcher assertion)

**Interfaces:**
- Consumes: existing `run_claude_with_router`, `generate_ccr_config`, `model_registry` (gateway reads `CLAUDE_CODEX_FLAVOR`).
- Produces: when invoked as `claude-reasonix`, the launcher sets `CLAUDE_CODEX_FLAVOR=reasonix`, exposes alias model `claude-reasonix-flash`, default model `deepseek-v4-flash`, and uses the reasonix system-prompt; `router-login` etc. otherwise unchanged. When invoked as `claude-codex`, behavior is identical to today.

- [ ] **Step 1: Add flavor detection near the top of `~/.local/bin/claude-codex`**

After the existing top-level variable block (near `CCR_PROXY_URL=""`, ~line 31), add:

```bash
# Flavor: 'codex' (default) or 'reasonix', selected by the invoked command name.
CLAUDE_CODEX_FLAVOR="${CLAUDE_CODEX_FLAVOR:-}"
case "$(basename "$0")" in
  claude-reasonix) CLAUDE_CODEX_FLAVOR="reasonix" ;;
  *) CLAUDE_CODEX_FLAVOR="${CLAUDE_CODEX_FLAVOR:-codex}" ;;
esac
export CLAUDE_CODEX_FLAVOR
if [[ "$CLAUDE_CODEX_FLAVOR" == "reasonix" ]]; then
  : "${CLAUDE_CODEX_CCR_CODEX_MODEL:=claude-reasonix-flash}"
  : "${CLAUDE_CODEX_SUBAGENT_MODEL:=claude-reasonix-flash}"
  : "${ANTHROPIC_CUSTOM_MODEL_OPTION:=claude-reasonix-flash}"
  export CLAUDE_CODEX_CCR_CODEX_MODEL CLAUDE_CODEX_SUBAGENT_MODEL ANTHROPIC_CUSTOM_MODEL_OPTION
fi
```

- [ ] **Step 2: Point the reasonix system-prompt (in the prompt-selection code)**

Find where `router_prompt()` / `PROMPT_FILE` is resolved (the `run_claude_with_router` path, ~line 879 `prompt="$(router_prompt)"`). Make the prompt file flavor-aware: in `router_prompt` (or just before it), if `CLAUDE_CODEX_FLAVOR=reasonix` and `system-prompt-reasonix.md` exists, use it; else the existing prompt. Minimal edit:

```bash
  local prompt
  if [[ "$CLAUDE_CODEX_FLAVOR" == "reasonix" && -f "$INSTALL_HOME/system-prompt-reasonix.md" ]]; then
    prompt="$(cat "$INSTALL_HOME/system-prompt-reasonix.md")"
  else
    prompt="$(router_prompt)"
  fi
```

- [ ] **Step 3: Create the symlink**

```bash
ln -sf "$HOME/.local/bin/claude-codex" "$HOME/.local/bin/claude-reasonix"
```

- [ ] **Step 4: Add a launcher assertion to the suite**

In `tests/test-codex-fleet.sh`, add a check that invoking the launcher as reasonix flavor advertises `claude-reasonix-flash`. Reuse the existing pattern that runs the launcher and greps its generated args. Concretely, add a python or grep block asserting that with `CLAUDE_CODEX_FLAVOR=reasonix`, `model_registry()` exposes `claude-reasonix-flash` and `provider=reasonix_cli` (this also covers the gateway side):

```bash
CLAUDE_CODEX_FLAVOR=reasonix python3 - "$GATEWAY" <<'PY' || fail "reasonix flavor must expose claude-reasonix-flash"
import importlib.util, sys
spec = importlib.util.spec_from_file_location("g", sys.argv[1])
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
reg = g.model_registry()
assert "claude-reasonix-flash" in reg, list(reg)
assert reg["claude-reasonix-flash"]["provider"] == "reasonix_cli"
PY
```

- [ ] **Step 5: Run full regression**

Run: `bash tests/test-codex-fleet.sh` → exit 0

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/codex-fleet && git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add tests/test-codex-fleet.sh && git commit -m "feat(launcher): claude-reasonix flavor + flash model exposure" || echo "skip commit"
# launcher + symlink live in ~/.local/bin (outside the repo) — note manual install
```

---

### Task 5: Reasonix system-prompt (orchestration guidance)

**Files:**
- Create: `system-prompt-reasonix.md`
- Modify: `tests/test-codex-fleet.sh` (content assertions, mirroring the codex prompt checks)

**Interfaces:**
- Consumes: nothing (static file).
- Produces: `system-prompt-reasonix.md` used by the launcher in reasonix flavor.

- [ ] **Step 1: Add failing content assertions**

In `tests/test-codex-fleet.sh`, mirror the existing `system-prompt.md` grep checks for the reasonix prompt:

```bash
RX_PROMPT="$ROOT/system-prompt-reasonix.md"
[[ -f "$RX_PROMPT" ]] || fail "missing reasonix system prompt"
grep -q "claude-reasonix-flash" "$RX_PROMPT" || fail "reasonix prompt must name the flash agent"
grep -q "atomic" "$RX_PROMPT" || fail "reasonix prompt must teach atomic-task decomposition"
grep -q "unlimited" "$RX_PROMPT" || fail "reasonix prompt must state agent count is unlimited"
grep -q "web search" "$RX_PROMPT" || fail "reasonix prompt must mention built-in web search"
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash tests/test-codex-fleet.sh` → FAIL "missing reasonix system prompt"

- [ ] **Step 3: Write `system-prompt-reasonix.md`**

```markdown
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
- **Web search is available inside a lane** as a built-in tool — a Reasonix lane
  can research the web on its own; no special flag needed.
- Reasonix lanes write real files in the workspace (yolo mode).

## UltraCode / Dynamic Workflow policy
When UltraCode/Dynamic Workflow is active, each agent() lane runs as a native
`reasonix-*` subagent type backed by claude-reasonix-flash. Do not spawn Claude
native subagents directly. This mode exposes only Reasonix agents (no codex-*).
```

- [ ] **Step 4: Run to verify it passes**

Run: `bash tests/test-codex-fleet.sh` → exit 0

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/codex-fleet && git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add system-prompt-reasonix.md tests/test-codex-fleet.sh && \
  git commit -m "feat: reasonix orchestration system-prompt" || echo "skip commit"
```

---

### Task 6: Workflow-hook reasonix agentTypes + self-heal presence check

**Files:**
- Modify: `hooks/codex-workflow.py` — emit `reasonix-*` agentTypes in reasonix flavor
- Modify: `hooks/workflow_selfheal.py` — reasonix-CLI presence check
- Modify: `tests/test-workflow-selfheal.py` (assertions)

**Interfaces:**
- Consumes: `CLAUDE_CODEX_FLAVOR` env, existing `wrapper_source_native`, `_candidate_health_urls`.
- Produces: in reasonix flavor, the injected wrapper maps lanes to `reasonix-worker`/`reasonix-verify`/`reasonix-research` (all → claude-reasonix-flash); self-heal reports a `reasonix_cli` presence check.

- [ ] **Step 1: Write failing tests**

In `tests/test-workflow-selfheal.py`, add a test that sets `CLAUDE_CODEX_FLAVOR=reasonix` and asserts the native wrapper source (from `codex-workflow.py`'s `wrapper_source_native()`) contains `reasonix-worker` and does NOT force codex-only agentTypes. (Load `codex-workflow.py` the same way the existing test loads it.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test-workflow-selfheal.py` → FAIL (wrapper has no reasonix-worker).

- [ ] **Step 3: Make `wrapper_source_native()` flavor-aware**

In `hooks/codex-workflow.py`, read `os.getenv("CLAUDE_CODEX_FLAVOR", "codex")`. When `reasonix`, the `__claudeCodexNativeAgentType` function returns `reasonix-*` types: `reasonix-security`/`reasonix-verify`/`reasonix-reviewer`/`reasonix-research` by the same hint rules, default `reasonix-worker`; when `codex`, keep today's `codex-*`/`deepseek-*` behavior unchanged. Inject the flavor into the generated JS as a `const __claudeCodexFlavor = '<flavor>'` line and branch on it inside `__claudeCodexNativeAgentType`.

- [ ] **Step 4: Add reasonix presence check to self-heal**

In `hooks/workflow_selfheal.py` `preflight`, when `os.getenv("CLAUDE_CODEX_FLAVOR")=="reasonix"`, add `report["checks"]["reasonix_cli"] = {"present": bool(shutil.which(os.getenv("REASONIX_BIN","reasonix")))}` and, if absent, append a SELF-HEAL note telling the user to install/login Reasonix CLI. (Import `shutil`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 tests/test-workflow-selfheal.py` → PASS
Run: `bash tests/test-codex-fleet.sh` → exit 0

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/codex-fleet && git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add hooks/codex-workflow.py hooks/workflow_selfheal.py tests/test-workflow-selfheal.py && \
  git commit -m "feat(hooks): reasonix agentTypes + reasonix-cli self-heal check" || echo "skip commit"
```

---

### Task 7: End-to-end smoke (real reasonix, real file write)

**Files:**
- Create: `tests/test-reasonix-e2e.sh` (opt-in, requires real reasonix login)
- Modify: `tests/test-codex-fleet.sh` (call e2e only when `CLAUDE_CODEX_REASONIX_E2E=1`)

**Interfaces:**
- Consumes: the full stack from Tasks 1–6.
- Produces: a guarded smoke test that starts a gateway, sends a `/v1/messages` request for `claude-reasonix-flash` asking to create a file, and asserts the file appears and the SSE stream had heartbeats.

- [ ] **Step 1: Write the e2e smoke script**

Create `tests/test-reasonix-e2e.sh`: start `codex-native-gateway.py` on a temp port, POST a non-stream `/v1/messages` for `claude-reasonix-flash` with prompt "Create file rx.txt containing PONG", read the SSE response, assert `event: message_start` present AND `rx.txt` exists with `PONG`. Skip with a clear message if `reasonix` is not on PATH or not logged in.

- [ ] **Step 2: Guard it in the suite**

In `tests/test-codex-fleet.sh`:

```bash
if [[ "${CLAUDE_CODEX_REASONIX_E2E:-0}" == "1" ]]; then
  bash "$ROOT/tests/test-reasonix-e2e.sh" || fail "reasonix e2e"
else
  echo "SKIP: reasonix e2e (set CLAUDE_CODEX_REASONIX_E2E=1 to run)"
fi
```

- [ ] **Step 3: Run the unit suite (e2e skipped)**

Run: `bash tests/test-codex-fleet.sh` → exit 0, prints SKIP line.

- [ ] **Step 4: Run the e2e manually once**

Run: `CLAUDE_CODEX_REASONIX_E2E=1 bash tests/test-codex-fleet.sh`
Expected: reasonix actually writes `rx.txt=PONG`; SSE had heartbeats. (Requires reasonix login.)

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/codex-fleet && git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add tests/test-reasonix-e2e.sh tests/test-codex-fleet.sh && \
  git commit -m "test: reasonix end-to-end smoke (opt-in)" || echo "skip commit"
```

---

## Manual install / activation (after all tasks)

1. `ln -sf ~/.local/bin/claude-codex ~/.local/bin/claude-reasonix` (Task 4 does this).
2. In a tmux pane: `claude-reasonix router-login --dangerously-skip-permissions`, then `/rc`.
3. Verify: ask it to do a small coding task; confirm a Reasonix lane writes a file
   and the lane does NOT die at 180s (heartbeat chain inherited from the codex fixes).
