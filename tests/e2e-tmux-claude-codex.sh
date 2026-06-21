#!/usr/bin/env bash
set -euo pipefail

ROOT="${CLAUDE_CODEX_FLEET_INSTALL_HOME:-/Users/tatlatat/.claude/codex-fleet}"
LAUNCHER="${CLAUDE_CODEX_LAUNCHER:-/Users/tatlatat/.local/bin/claude-codex}"
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || true)}"
CODEX_BIN="${CODEX_BIN:-$(command -v codex || true)}"
CCR_BIN="${CCR_BIN:-$(command -v ccr || true)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TMUX_BIN="${TMUX_BIN:-tmux}"
TIMEOUT_BIN="${TIMEOUT_BIN:-timeout}"

SESSION="${CLAUDE_CODEX_E2E_SESSION:-claude-codex-e2e-$(date +%H%M%S)}"
RUN_DIR="${CLAUDE_CODEX_E2E_RUN_DIR:-/tmp/$SESSION}"
WORKDIR="$RUN_DIR/workspace"
FLEET_HOME="$RUN_DIR/fleet"
STEP_DIR="$RUN_DIR/steps"
STATUS_DIR="$RUN_DIR/status"
LOG_DIR="$RUN_DIR/logs"
CODEX_LOG_DIR="$LOG_DIR/gateway-codex-logs"
EVIDENCE_VERIFY="$ROOT/tests/verify-e2e-evidence.py"
CODEX_E2E_MODEL="${CODEX_E2E_MODEL:-${CLAUDE_CODEX_CODEX_MODEL:-${CODEX_FLEET_MODEL:-gpt-5.4}}}"

RUN_DEEP_RESEARCH=0
RUN_ULTRACODE=0
KEEP_SESSION=0
START_MS="$("$PYTHON_BIN" - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"

usage() {
  cat <<'EOF'
Usage: e2e-tmux-claude-codex.sh [--deep-research] [--ultracode] [--keep-session]

Runs a tmux-backed claude-codex E2E harness with isolated runtime/log dirs.
Default checks:
  - claude-codex doctor
  - Claude login print mode
  - Codex CLI configured model print mode
  - direct native gateway -> codex exec
  - claude-codex router-login native codex-worker subagent
  - real Workflow/Dynamic Workflow one-agent lane routed to claude-codex-pro

Optional:
  --deep-research  Run built-in /deep-research smoke with longer timeout.
  --ultracode      Run a minimal ultracode keyword smoke.
  --keep-session   Leave the tmux session open after the harness finishes.

Model override:
  CODEX_E2E_MODEL=gpt-5.4 e2e-tmux-claude-codex.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep-research) RUN_DEEP_RESEARCH=1 ;;
    --ultracode) RUN_ULTRACODE=1 ;;
    --keep-session) KEEP_SESSION=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

fail() {
  echo "FAIL: $*" >&2
  echo "run dir: $RUN_DIR" >&2
  exit 1
}

need_file() {
  [[ -f "$1" ]] || fail "missing file: $1"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

quote() {
  printf '%q' "$1"
}

prepare() {
  need_file "$LAUNCHER"
  need_file "$ROOT/codex-native-gateway.py"
  need_file "$ROOT/ccr-claude-proxy.py"
  need_file "$EVIDENCE_VERIFY"
  [[ -n "$CLAUDE_BIN" && -x "$CLAUDE_BIN" ]] || fail "missing executable Claude binary"
  [[ -n "$CODEX_BIN" && -x "$CODEX_BIN" ]] || fail "missing executable Codex binary"
  [[ -n "$CCR_BIN" && -x "$CCR_BIN" ]] || fail "missing executable CCR binary"
  need_cmd "$PYTHON_BIN"
  need_cmd "$TMUX_BIN"
  need_cmd "$TIMEOUT_BIN"

  if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    fail "tmux session already exists: $SESSION"
  fi

  mkdir -p "$WORKDIR" "$FLEET_HOME" "$STEP_DIR" "$STATUS_DIR" "$LOG_DIR" "$CODEX_LOG_DIR"
}

start_tmux() {
  local tmux_env
  tmux_env=(
    env
    "CLAUDE_CODEX_FLEET_HOME=$FLEET_HOME"
    "CLAUDE_CODEX_KEEP_ROUTER_RUNTIME=1"
    "CLAUDE_CODEX_GATEWAY_TRACE=1"
    "CLAUDE_CODEX_CCR_PROXY_TRACE=1"
    "CLAUDE_CODEX_GATEWAY_CODEX_LOG_DIR=$CODEX_LOG_DIR"
    "CLAUDE_CODEX_GATEWAY_CODEX_CONCURRENCY=${CLAUDE_CODEX_GATEWAY_CODEX_CONCURRENCY:-16}"
    "CLAUDE_CODEX_GATEWAY_CODEX_MAX_ATTEMPTS=${CLAUDE_CODEX_GATEWAY_CODEX_MAX_ATTEMPTS:-4}"
    "CLAUDE_CODEX_GATEWAY_CODEX_RETRY_BASE_SECONDS=${CLAUDE_CODEX_GATEWAY_CODEX_RETRY_BASE_SECONDS:-8}"
    "CODEX_E2E_MODEL=$CODEX_E2E_MODEL"
    "CLAUDE_CODEX_CODEX_MODEL=$CODEX_E2E_MODEL"
    "CODEX_FLEET_MODEL=$CODEX_E2E_MODEL"
    "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0"
    "CLAUDE_BIN=$CLAUDE_BIN"
    "CODEX_BIN=$CODEX_BIN"
    "CCR_BIN=$CCR_BIN"
    "PATH=$PATH"
    zsh
    -f
  )
  "$TMUX_BIN" new-session -d -s "$SESSION" -c "$WORKDIR" "${tmux_env[@]}"
}

stop_tmux() {
  if [[ "$KEEP_SESSION" == "1" ]]; then
    return 0
  fi
  "$TMUX_BIN" kill-session -t "$SESSION" 2>/dev/null || true
}

write_step_script() {
  local name="$1"
  local seconds="$2"
  local command="$3"
  local script="$STEP_DIR/$name.sh"
  local out="$STEP_DIR/$name.out"
  local status="$STATUS_DIR/$name.status"

  {
    printf '#!/usr/bin/env bash\n'
    printf 'set +e\n'
    printf 'cd %s || exit 97\n' "$(quote "$WORKDIR")"
    printf 'export CLAUDE_CODEX_FLEET_HOME=%s\n' "$(quote "$FLEET_HOME")"
    printf 'export CLAUDE_CODEX_KEEP_ROUTER_RUNTIME=1\n'
    printf 'export CLAUDE_CODEX_GATEWAY_TRACE=1\n'
    printf 'export CLAUDE_CODEX_CCR_PROXY_TRACE=1\n'
    printf 'export CLAUDE_CODEX_GATEWAY_CODEX_LOG_DIR=%s\n' "$(quote "$CODEX_LOG_DIR")"
    printf 'export CLAUDE_CODEX_GATEWAY_CODEX_CONCURRENCY=%s\n' "$(quote "${CLAUDE_CODEX_GATEWAY_CODEX_CONCURRENCY:-16}")"
    printf 'export CLAUDE_CODEX_GATEWAY_CODEX_MAX_ATTEMPTS=%s\n' "$(quote "${CLAUDE_CODEX_GATEWAY_CODEX_MAX_ATTEMPTS:-4}")"
    printf 'export CLAUDE_CODEX_GATEWAY_CODEX_RETRY_BASE_SECONDS=%s\n' "$(quote "${CLAUDE_CODEX_GATEWAY_CODEX_RETRY_BASE_SECONDS:-8}")"
    printf 'export CODEX_E2E_MODEL=%s\n' "$(quote "$CODEX_E2E_MODEL")"
    printf 'export CLAUDE_CODEX_CODEX_MODEL=%s\n' "$(quote "$CODEX_E2E_MODEL")"
    printf 'export CODEX_FLEET_MODEL=%s\n' "$(quote "$CODEX_E2E_MODEL")"
    printf 'export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0\n'
    printf 'export CLAUDE_BIN=%s\n' "$(quote "$CLAUDE_BIN")"
    printf 'export CODEX_BIN=%s\n' "$(quote "$CODEX_BIN")"
    printf 'export CCR_BIN=%s\n' "$(quote "$CCR_BIN")"
    printf 'export PATH=%s\n' "$(quote "$PATH")"
    printf 'exec >%s 2>&1\n' "$(quote "$out")"
    printf 'printf "__STEP_START__ %s %%s\\n" "$(date +%%Y-%%m-%%dT%%H:%%M:%%S%%z)"\n' "$name"
    printf '%s %s bash -lc %s\n' "$(quote "$TIMEOUT_BIN")" "$(quote "$seconds")" "$(quote "$command")"
    printf 'code=$?\n'
    printf 'printf "__STEP_EXIT__ %s %%s %%s\\n" "$code" "$(date +%%Y-%%m-%%dT%%H:%%M:%%S%%z)"\n' "$name"
    printf 'printf "%%s\\n" "$code" >%s\n' "$(quote "$status")"
    printf 'exit "$code"\n'
  } >"$script"
  chmod +x "$script"
}

run_step() {
  local name="$1"
  local seconds="$2"
  local command="$3"
  local status="$STATUS_DIR/$name.status"
  local pane="$STEP_DIR/$name.pane.txt"

  rm -f "$status"
  write_step_script "$name" "$seconds" "$command"
  "$TMUX_BIN" send-keys -t "$SESSION" "bash $(quote "$STEP_DIR/$name.sh")" C-m

  local waited=0
  local max_wait=$((seconds + 120))
  while [[ ! -f "$status" && "$waited" -lt "$max_wait" ]]; do
    sleep 1
    waited=$((waited + 1))
  done
  "$TMUX_BIN" capture-pane -t "$SESSION" -p -S -400 >"$pane" 2>/dev/null || true

  if [[ ! -f "$status" ]]; then
    echo "step $name did not finish after ${max_wait}s" >&2
    return 124
  fi

  local code
  code="$(tr -d '[:space:]' <"$status")"
  [[ "$code" =~ ^[0-9]+$ ]] || code=125
  return "$code"
}

require_contains() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if ! rg -q "$pattern" "$file"; then
    echo "missing pattern '$pattern' in $file" >&2
    tail -n 120 "$file" >&2 || true
    fail "$label"
  fi
}

verify_codex_cli_logs() {
  "$PYTHON_BIN" "$EVIDENCE_VERIFY" codex-logs \
    --codex-log-dir "$CODEX_LOG_DIR" \
    --expected-model "$CODEX_E2E_MODEL"
}

verify_router_logs() {
  "$PYTHON_BIN" "$EVIDENCE_VERIFY" proxy \
    --proxy-log "$FLEET_HOME/runtime/ccr-proxy.log" \
    --require-main \
    --require-subagent \
    --require-codex-alias
}

verify_gateway_logs() {
  # --max-controlled-timeouts defaults to 0: any timeout fallback (a Codex agent
  # killed mid-work and served empty results) fails for EVERY plain step. We do NOT
  # require a parsed structured success here, because plain-text smoke steps
  # (router_subagent, dynamic_workflow) legitimately produce text responses with
  # parsed_json_object=false. Extra flags are forwarded verbatim, so structured
  # workflows can add --require-parsed-success and a small --max-controlled-timeouts
  # tolerance for transient slow web-fetch lanes that the workflow itself retries.
  "$PYTHON_BIN" "$EVIDENCE_VERIFY" gateway \
    --gateway-log "$FLEET_HOME/runtime/gateway.log" \
    "$@"
}

verify_workflow_marker() {
  local marker="$1"
  local expected_name="${2:-}"
  local expected_status="${3:-}"
  local min_agents="${4:-1}"
  local min_nonempty="${5:-1}"
  "$PYTHON_BIN" "$EVIDENCE_VERIFY" workflow \
    --projects-root "$HOME/.claude/projects" \
    --marker "$marker" \
    --start-ms "$START_MS" \
    --expected-name "$expected_name" \
    --expected-status "$expected_status" \
    --min-agents "$min_agents" \
    --min-nonempty-agents "$min_nonempty" \
    --require-all-codex
}

run_router_subagent() {
  local prompt='Use the codex-worker subagent exactly once. Ask that worker to reply exactly CODEX_TRACE_SUBAGENT_OK and do no other work. Then return the worker result.'
  local command
  command="$(printf '%q ' "$LAUNCHER" router-login -p "$prompt" --permission-mode bypassPermissions)"
  if ! run_step router_subagent 420 "$command"; then
    if rg -q '529|Overloaded' "$STEP_DIR/router_subagent.out"; then
      echo "router_subagent hit temporary 529; retrying once" >&2
      run_step router_subagent_retry 420 "$command" || return $?
      require_contains "$STEP_DIR/router_subagent_retry.out" "CODEX_TRACE_SUBAGENT_OK" "router-login subagent retry did not return marker"
      return 0
    fi
    return 1
  fi
  require_contains "$STEP_DIR/router_subagent.out" "CODEX_TRACE_SUBAGENT_OK" "router-login subagent did not return marker"
}

run_dynamic_workflow() {
  local prompt='Use the real Workflow tool, not Bash, to run a minimal workflow named codex-e2e-workflow. It must have one phase named Test and one agent lane. The agent prompt must be: Reply exactly WORKFLOW_CODEX_E2E_OK and nothing else. The agent options must include label codex:e2e and phase Test. Return only the workflow result.'
  local command
  command="$(printf '%q ' "$LAUNCHER" router-login -p "$prompt" --permission-mode bypassPermissions)"
  run_step dynamic_workflow 720 "$command"
  require_contains "$STEP_DIR/dynamic_workflow.out" "WORKFLOW_CODEX_E2E_OK" "Dynamic Workflow did not return marker"
  verify_workflow_marker "WORKFLOW_CODEX_E2E_OK" "codex-e2e-workflow" "completed"
}

run_deep_research() {
  # The built-in deep-research workflow fans out up to 75 verify agents
  # (MAX_VERIFY_CLAIMS=25 x VOTES_PER_CLAIM=3). Through slow Codex lanes that
  # exceeds a 30-min budget. We cannot edit the baked-in built-in, so we (a) scope
  # the question narrowly to keep the claim count (and thus the Verify fan-out)
  # small, and (b) give the step a 60-min budget so Verify + Synthesize complete.
  local prompt='Use the real Workflow tool to run the built-in workflow named deep-research. Pass this exact args string: Narrow smoke-test research question (keep it minimal): name at most 3 concrete things the claude-codex router mode must prove for subagents. Constrain the work: at most 3 search angles, fetch at most 4 sources, extract at most 2 central claims total, and verify with a single pass (do not over-fan-out). Produce a very short cited report and include the exact marker DEEP_RESEARCH_CODEX_E2E_OK in the final report. Do not use Bash; use Workflow.'
  local command
  command="$(printf '%q ' "$LAUNCHER" router-login -p "$prompt" --permission-mode bypassPermissions)"
  run_step deep_research 3600 "$command"
  require_contains "$STEP_DIR/deep_research.out" "DEEP_RESEARCH_CODEX_E2E_OK" "Deep Research did not return marker"
  # Narrow scope (<=3 angles) still yields scope + searches + fetches + verifies, so
  # >= 5 total agents is easily met. Require >= 3 non-empty (scope + at least two
  # search/fetch lanes with content) so a hollow run still fails, without assuming
  # the larger 5-angle fan-out we deliberately suppressed here.
  verify_workflow_marker "DEEP_RESEARCH_CODEX_E2E_OK" "deep-research" "completed" 5 3
  # Deep research forces StructuredOutput, so its gateway log MUST show a parsed
  # structured success. Allow up to 3 transient timeout fallbacks: the large real
  # fan-out can have a couple of slow web-fetch lanes hit the 600s ceiling, but the
  # workflow retries them — the workflow verifier above already proves the OUTCOME
  # is clean (every agent state=done, >= min-nonempty results, no empty finals), so
  # a recovered transient timeout is not a hollow run.
  verify_gateway_logs --require-parsed-success --max-controlled-timeouts 3
}

run_ultracode() {
  local prompt='ultracode: Use the real Workflow tool, not Bash, to run a minimal workflow named ultracode-codex-e2e. It must have one phase named UltraSmoke and one Codex-backed worker lane. The worker prompt must be: Reply exactly ULTRACODE_CODEX_E2E_OK and nothing else. Return only the workflow result marker.'
  local command
  command="$(printf '%q ' "$LAUNCHER" router-login -p "$prompt" --permission-mode bypassPermissions)"
  run_step ultracode 900 "$command"
  require_contains "$STEP_DIR/ultracode.out" "ULTRACODE_CODEX_E2E_OK" "UltraCode smoke did not return marker"
  verify_workflow_marker "ULTRACODE_CODEX_E2E_OK" "ultracode-codex-e2e" "completed"
}

main() {
  prepare
  start_tmux
  trap stop_tmux EXIT

  echo "tmux session: $SESSION"
  echo "run dir: $RUN_DIR"
  echo "codex e2e model: $CODEX_E2E_MODEL"

  run_step doctor 120 "$(printf '%q ' "$LAUNCHER" doctor)"
  require_contains "$STEP_DIR/doctor.out" "codex fleet doctor: ok" "doctor failed"

  run_step claude_login 180 "$(printf '%q ' "$CLAUDE_BIN" -p "Reply exactly CLAUDE_LOGIN_OK" --permission-mode bypassPermissions)"
  require_contains "$STEP_DIR/claude_login.out" "CLAUDE_LOGIN_OK" "Claude login probe failed"

  local codex_login_cmd
  codex_login_cmd="$(cat <<EOF
set +e
last_code=1
for attempt in 1 2 3 4; do
  "$CODEX_BIN" exec --skip-git-repo-check -m "$CODEX_E2E_MODEL" "Reply exactly CODEX_MODEL_OK"
  last_code=\$?
  if [[ "\$last_code" == "0" ]]; then
    exit 0
  fi
  if [[ "\$attempt" == "4" ]]; then
    break
  fi
  echo "codex_login attempt \$attempt failed with \$last_code; retrying after backoff" >&2
  sleep \$((attempt * 20))
done
exit "\$last_code"
EOF
)"
  run_step codex_login 420 "$codex_login_cmd"
  require_contains "$STEP_DIR/codex_login.out" "CODEX_MODEL_OK" "Codex CLI probe failed"

  local gateway_cmd
  gateway_cmd="$(cat <<EOF
set -euo pipefail
port_file="$RUN_DIR/direct-gateway.port"
log_file="$LOG_DIR/direct-gateway.log"
rm -f "\$port_file"
"$PYTHON_BIN" "$ROOT/codex-native-gateway.py" --host 127.0.0.1 --port 0 --port-file "\$port_file" >>"\$log_file" 2>&1 &
gateway_pid=\$!
trap 'kill "\$gateway_pid" 2>/dev/null || true; wait "\$gateway_pid" 2>/dev/null || true' EXIT
for _ in {1..120}; do
  [[ -s "\$port_file" ]] && break
  kill -0 "\$gateway_pid" 2>/dev/null || { cat "\$log_file"; exit 1; }
  sleep 0.1
done
[[ -s "\$port_file" ]]
port=\$(tr -d '[:space:]' <"\$port_file")
"$PYTHON_BIN" - "\$port" <<'PY'
import json
import sys
import urllib.request

port = sys.argv[1]
body = json.dumps({
    "model": "claude-codex-pro",
    "messages": [{"role": "user", "content": "Reply exactly GATEWAY_CODEX_OK"}],
}).encode()
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/chat/completions",
    data=body,
    headers={"content-type": "application/json"},
)
data = json.load(urllib.request.urlopen(req, timeout=600))
content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
print(content)
if "GATEWAY_CODEX_OK" not in content:
    raise SystemExit(data)
PY
EOF
)"
  run_step direct_gateway 720 "$gateway_cmd"
  require_contains "$STEP_DIR/direct_gateway.out" "GATEWAY_CODEX_OK" "direct gateway did not return marker"
  verify_codex_cli_logs

  run_router_subagent
  verify_router_logs
  verify_gateway_logs
  verify_codex_cli_logs

  run_dynamic_workflow
  verify_router_logs
  verify_gateway_logs
  verify_codex_cli_logs

  if [[ "$RUN_DEEP_RESEARCH" == "1" ]]; then
    run_deep_research
    verify_router_logs
    verify_gateway_logs
    verify_codex_cli_logs
  fi

  if [[ "$RUN_ULTRACODE" == "1" ]]; then
    run_ultracode
    verify_router_logs
    verify_gateway_logs
    verify_codex_cli_logs
  fi

  echo "PASS: claude-codex tmux E2E"
  echo "run dir: $RUN_DIR"
}

main "$@"
