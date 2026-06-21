#!/usr/bin/env bash
set -euo pipefail

ROOT="${CLAUDE_CODEX_FLEET_INSTALL_HOME:-/Users/tatlatat/.claude/codex-fleet}"
VERIFY="$ROOT/tests/verify-e2e-evidence.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

projects="$tmp_dir/projects"
runtime="$tmp_dir/runtime"
codex_logs="$tmp_dir/codex-logs"
mkdir -p "$projects/session/workflows" "$projects/session/subagents/workflows/wf_bad" "$runtime" "$codex_logs"

cat >"$projects/session/workflows/wf_bad.json" <<'JSON'
{
  "runId": "wf_bad",
  "workflowName": "mixed-workflow",
  "status": "completed",
  "startTime": 1781860000000,
  "result": {"summary": "MIXED_MARKER"},
  "workflowProgress": [
    {
      "type": "workflow_agent",
      "agentId": "a_codex",
      "model": "claude-codex-pro",
      "state": "done"
    },
    {
      "type": "workflow_agent",
      "agentId": "a_opus",
      "model": "claude-opus-4-8",
      "state": "done"
    }
  ]
}
JSON

if "$PYTHON_BIN" "$VERIFY" workflow \
  --projects-root "$projects" \
  --marker MIXED_MARKER \
  --start-ms 1781859999000 \
  --expected-name mixed-workflow \
  --expected-status completed \
  --require-all-codex \
  >/tmp/claude-codex-mixed-workflow.out 2>&1; then
  cat /tmp/claude-codex-mixed-workflow.out >&2 || true
  fail "workflow verifier accepted a mixed Opus/Codex workflow"
fi

cat >"$projects/session/workflows/wf_good.json" <<'JSON'
{
  "runId": "wf_good",
  "workflowName": "codex-workflow",
  "status": "completed",
  "startTime": 1781860001000,
  "result": {"summary": "CODEX_MARKER"},
  "workflowProgress": [
    {
      "type": "workflow_agent",
      "agentId": "a_codex_1",
      "model": "claude-codex-pro",
      "state": "done",
      "resultPreview": "{\"results\":[{\"claim\":\"x\",\"quote\":\"y\",\"importance\":\"central\"}]}"
    },
    {
      "type": "workflow_agent",
      "agentId": "a_codex_2",
      "model": "claude-codex-pro",
      "state": "done",
      "resultPreview": "{\"results\":[{\"claim\":\"z\",\"quote\":\"w\",\"importance\":\"central\"}]}"
    }
  ]
}
JSON

"$PYTHON_BIN" "$VERIFY" workflow \
  --projects-root "$projects" \
  --marker CODEX_MARKER \
  --start-ms 1781859999000 \
  --expected-name codex-workflow \
  --expected-status completed \
  --require-all-codex \
  --min-agents 2 \
  >/dev/null

# Hollow run: status=completed, all agents state=done, but every result is the
# empty timeout-fallback shape {"results":[]}. Must be REJECTED so a workflow that
# only "completed" by serving empty schema objects cannot pass.
cat >"$projects/session/workflows/wf_hollow.json" <<'JSON'
{
  "runId": "wf_hollow",
  "workflowName": "deep-research",
  "status": "completed",
  "startTime": 1781860002000,
  "result": {"summary": "HOLLOW_MARKER"},
  "workflowProgress": [
    {
      "type": "workflow_agent",
      "agentId": "a_plan",
      "model": "claude-codex-pro",
      "state": "done",
      "resultPreview": "{\"question\":\"q\",\"angles\":[{\"label\":\"a\"}]}"
    },
    {
      "type": "workflow_agent",
      "agentId": "a_search_1",
      "model": "claude-codex-pro",
      "state": "done",
      "resultPreview": "{\"results\":[]}"
    },
    {
      "type": "workflow_agent",
      "agentId": "a_search_2",
      "model": "claude-codex-pro",
      "state": "done",
      "resultPreview": "{\"results\":[]}"
    }
  ]
}
JSON

if "$PYTHON_BIN" "$VERIFY" workflow \
  --projects-root "$projects" \
  --marker HOLLOW_MARKER \
  --start-ms 1781859999000 \
  --expected-name deep-research \
  --expected-status completed \
  --require-all-codex \
  --min-agents 3 \
  --min-nonempty-agents 2 \
  >/tmp/claude-codex-hollow-workflow.out 2>&1; then
  cat /tmp/claude-codex-hollow-workflow.out >&2 || true
  fail "workflow verifier accepted a hollow run (empty agent results)"
fi

cat >"$runtime/ccr-proxy.log" <<'JSONL'
{"event":"forward","model":"claude-opus-4-8","has_subagent_context":false,"has_subagent_tag":false,"route_target":"main","target":"https://api.anthropic.com"}
{"event":"forward","model":"claude-opus-4-8","has_subagent_context":true,"has_subagent_tag":false,"route_target":"main","target":"https://api.anthropic.com"}
JSONL

if "$PYTHON_BIN" "$VERIFY" proxy \
  --proxy-log "$runtime/ccr-proxy.log" \
  --require-main \
  --require-subagent \
  >/tmp/claude-codex-bad-proxy.out 2>&1; then
  cat /tmp/claude-codex-bad-proxy.out >&2 || true
  fail "proxy verifier accepted a subagent routed to main"
fi

cat >"$runtime/ccr-proxy.log" <<'JSONL'
{"event":"forward","model":"claude-opus-4-8","has_subagent_context":false,"has_subagent_tag":false,"route_target":"main","target":"https://api.anthropic.com"}
{"event":"forward","model":"claude-codex-pro","has_subagent_context":true,"has_subagent_tag":false,"route_target":"ccr","target":"http://127.0.0.1:52091"}
{"event":"response","model":"claude-codex-pro","has_subagent_context":true,"has_subagent_tag":false,"route_target":"ccr","status":200}
JSONL

"$PYTHON_BIN" "$VERIFY" proxy \
  --proxy-log "$runtime/ccr-proxy.log" \
  --require-main \
  --require-subagent \
  >/dev/null

cat >"$runtime/gateway.log" <<'JSONL'
{"event":"codex_cli_start","model":"gpt-5.4","attempt":1}
{"event":"codex_cli_anthropic_response","model":"claude-codex-pro","parsed_json_object":true}
JSONL

"$PYTHON_BIN" "$VERIFY" gateway --gateway-log "$runtime/gateway.log" --require-parsed-success >/dev/null

# Negative: a gateway log with a structured timeout fallback (a Codex agent killed
# mid-work, served empty results) must FAIL by default (--max-controlled-timeouts 0).
cat >"$runtime/gateway-timeout.log" <<'JSONL'
{"event":"codex_cli_start","model":"gpt-5.4","attempt":1}
{"event":"codex_cli_anthropic_structured_timeout_fallback","model":"claude-codex-pro","reason":"codex exec timed out after 165s","structured_tool":"StructuredOutput"}
JSONL

if "$PYTHON_BIN" "$VERIFY" gateway --gateway-log "$runtime/gateway-timeout.log" \
  >/tmp/claude-codex-gateway-timeout.out 2>&1; then
  cat /tmp/claude-codex-gateway-timeout.out >&2 || true
  fail "gateway verifier accepted a run with a structured timeout fallback"
fi

# Negative: a gateway log with NO parsed structured success must FAIL when
# --require-parsed-success is set.
cat >"$runtime/gateway-nosuccess.log" <<'JSONL'
{"event":"codex_cli_start","model":"gpt-5.4","attempt":1}
JSONL

if "$PYTHON_BIN" "$VERIFY" gateway --gateway-log "$runtime/gateway-nosuccess.log" --require-parsed-success \
  >/tmp/claude-codex-gateway-nosuccess.out 2>&1; then
  cat /tmp/claude-codex-gateway-nosuccess.out >&2 || true
  fail "gateway verifier accepted a run with no parsed structured success"
fi

expected_codex_model="${CODEX_E2E_MODEL:-${CLAUDE_CODEX_CODEX_MODEL:-${CODEX_FLEET_MODEL:-gpt-5.4}}}"
"$PYTHON_BIN" - "$codex_logs/command.command.json" "$expected_codex_model" <<'PY'
import json
import sys

path, expected_model = sys.argv[1:]
with open(path, "w", encoding="utf-8") as fh:
    json.dump({
        "command": [
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "-m",
            expected_model,
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="live"',
            "prompt",
        ]
    }, fh)
    fh.write("\n")
PY

"$PYTHON_BIN" "$VERIFY" codex-logs \
  --codex-log-dir "$codex_logs" \
  --expected-model "$expected_codex_model" \
  >/dev/null

if "$PYTHON_BIN" "$VERIFY" codex-logs \
  --codex-log-dir "$codex_logs" \
  --expected-model "definitely-not-$expected_codex_model" \
  >/tmp/claude-codex-wrong-model.out 2>&1; then
  cat /tmp/claude-codex-wrong-model.out >&2 || true
  fail "codex log verifier accepted the wrong expected model"
fi

echo "PASS: e2e evidence verifier"
