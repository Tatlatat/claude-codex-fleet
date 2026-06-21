#!/usr/bin/env python3
"""Evidence checks for claude-codex tmux E2E runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


JSON = dict[str, Any]


def die(message: str) -> None:
    raise SystemExit(message)


def load_json(path: Path) -> JSON:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        die(f"failed to parse JSON {path}: {exc}")
    if not isinstance(data, dict):
        die(f"expected object JSON in {path}")
    return data


def iter_jsonl(path: Path) -> list[JSON]:
    if not path.is_file():
        die(f"missing log: {path}")
    records: list[JSON] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip().startswith("{"):
            continue
        try:
            data = json.loads(line)
        except Exception as exc:
            die(f"failed to parse JSONL {path}:{lineno}: {exc}")
        if isinstance(data, dict):
            records.append(data)
    return records


def is_codex_agent(item: JSON) -> bool:
    model = str(item.get("model") or "")
    agent_type = str(item.get("agentType") or item.get("subagentType") or "")
    return model == "claude-codex-pro" or agent_type.startswith("codex-")


def workflow_schema_error_count(workflow_path: Path, run_id: str) -> int:
    session_dir = workflow_path.parent.parent
    subagent_dir = session_dir / "subagents" / "workflows" / run_id
    if not subagent_dir.is_dir():
        return 0
    count = 0
    for path in subagent_dir.glob("agent-*.jsonl"):
        count += path.read_text(encoding="utf-8", errors="replace").count("Output does not match required schema")
    return count


def is_hollow_result(value: Any) -> bool:
    """True when an agent result carries no substantive content.

    Catches the timeout-fallback shape ({"results": []} / {} / all-empty values /
    fallback sentinels) so a workflow that "completed" only by serving empty schema
    objects is not mistaken for a real research pass.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    if isinstance(value, dict):
        if not value:
            return True
        sentinels = {"unreliable", "unknown", "none", "n/a"}
        substantive = False
        for key, sub in value.items():
            if isinstance(sub, str) and sub.strip().lower() in sentinels:
                continue
            if is_hollow_result(sub):
                continue
            substantive = True
        return not substantive
    # numbers / bools: 0 and False are hollow, anything else is substantive
    return value in (0, False)


def agent_result_objects(item: JSON) -> Any:
    """Best-effort parse of a workflow_agent's emitted result from the wf JSON.

    The workflow JSON stores each agent's structured result in resultPreview; parse
    it as JSON when possible so emptiness can be inspected.
    """
    raw = item.get("resultPreview")
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        try:
            return json.loads(text)
        except Exception:
            # Truncated preview: treat a leading "{"results":[]}" style as parseable
            compact = text.replace(" ", "")
            if compact.startswith('{"results":[]}'):
                return {"results": []}
            return text
    return raw


def cmd_workflow(args: argparse.Namespace) -> None:
    root = Path(args.projects_root)
    if not root.is_dir():
        die(f"missing projects root: {root}")

    candidates: list[tuple[Path, JSON]] = []
    rejected: list[str] = []
    for path in root.glob("**/workflows/wf_*.json"):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if args.marker not in raw:
            continue
        try:
            data = json.loads(raw)
        except Exception as exc:
            rejected.append(f"{path}: invalid JSON: {exc}")
            continue
        if not isinstance(data, dict):
            rejected.append(f"{path}: workflow JSON was not an object")
            continue
        run_start = int(data.get("startTime") or 0)
        if run_start and run_start + 60000 < args.start_ms:
            continue
        if args.expected_name and data.get("workflowName") != args.expected_name:
            rejected.append(f"{path}: workflowName={data.get('workflowName')!r}")
            continue
        if args.expected_status and data.get("status") != args.expected_status:
            rejected.append(f"{path}: status={data.get('status')!r}")
            continue
        candidates.append((path, data))

    if not candidates:
        detail = "; ".join(rejected[-5:]) if rejected else "no marker match"
        die(f"no fresh workflow JSON matched marker={args.marker!r}: {detail}")

    failures: list[str] = []
    for path, data in sorted(candidates, key=lambda item: int(item[1].get("startTime") or 0), reverse=True):
        agents = [item for item in data.get("workflowProgress", []) if isinstance(item, dict) and item.get("type") == "workflow_agent"]
        if len(agents) < args.min_agents:
            failures.append(f"{path}: only {len(agents)} workflow agents, expected at least {args.min_agents}")
            continue
        unfinished = [item for item in agents if item.get("state") != "done"]
        if unfinished:
            failures.append(f"{path}: unfinished agents={len(unfinished)}")
            continue
        codex_agents = [item for item in agents if is_codex_agent(item)]
        if args.require_all_codex and len(codex_agents) != len(agents):
            non_codex = [
                {
                    "agentId": item.get("agentId"),
                    "model": item.get("model"),
                    "agentType": item.get("agentType"),
                    "state": item.get("state"),
                }
                for item in agents
                if not is_codex_agent(item)
            ]
            failures.append(f"{path}: non-Codex workflow agents found: {non_codex[:5]}")
            continue
        if not args.require_all_codex and not codex_agents:
            failures.append(f"{path}: no Codex-backed workflow agents")
            continue
        schema_errors = workflow_schema_error_count(path, str(data.get("runId") or path.stem))
        if schema_errors:
            failures.append(f"{path}: schema_errors={schema_errors}")
            continue
        # Reject hollow runs: a workflow can be status=completed with every agent
        # state=done yet have produced only empty schema objects (timeout fallback).
        # Require at least --min-nonempty-agents agents to carry substantive results.
        results = [agent_result_objects(item) for item in agents]
        nonempty = [r for r in results if not is_hollow_result(r)]
        if len(nonempty) < args.min_nonempty_agents:
            failures.append(
                f"{path}: hollow agent results — only {len(nonempty)}/{len(agents)} non-empty "
                f"(need >= {args.min_nonempty_agents}); empty={len(agents) - len(nonempty)}"
            )
            continue
        print(
            f"{path} status={data.get('status')} codex_agents={len(codex_agents)}/{len(agents)} "
            f"nonempty_agents={len(nonempty)}/{len(agents)}"
        )
        return

    die("workflow evidence failed: " + " | ".join(failures[-5:]))


def cmd_proxy(args: argparse.Namespace) -> None:
    records = iter_jsonl(Path(args.proxy_log))
    forwards = [record for record in records if record.get("event") == "forward"]
    if not forwards:
        die("proxy log has no forward events")

    errors: list[str] = []
    seen_main = False
    seen_subagent = False
    seen_codex_alias = False
    for record in records:
        event = str(record.get("event") or "")
        status = record.get("status")
        if event in {"http_error", "proxy_error"} or "bad_path" in event or "timeout" in event:
            errors.append(json.dumps(record, ensure_ascii=False)[:500])
        if isinstance(status, int) and status >= 400:
            errors.append(json.dumps(record, ensure_ascii=False)[:500])

    for record in forwards:
        model = str(record.get("model") or "")
        route = str(record.get("route_target") or "")
        target = str(record.get("target") or "")
        is_subagent = bool(record.get("has_subagent_context") or record.get("has_subagent_tag"))
        if route == "main" and not is_subagent:
            seen_main = True
        if is_subagent:
            seen_subagent = True
            if route != "ccr":
                errors.append(f"subagent routed outside CCR/native gateway: {record}")
            if "api.anthropic.com" in target:
                errors.append(f"subagent target was Anthropic main API: {record}")
        if model == "claude-codex-pro":
            seen_codex_alias = True
            if route != "ccr":
                errors.append(f"claude-codex-pro routed outside CCR/native gateway: {record}")

    if args.require_main and not seen_main:
        errors.append("no main Claude passthrough route observed")
    if args.require_subagent and not seen_subagent:
        errors.append("no Claude Code subagent-context route observed")
    if args.require_codex_alias and not seen_codex_alias:
        errors.append("no claude-codex-pro alias route observed")
    if errors:
        die("proxy evidence failed: " + " | ".join(errors[:8]))
    print(f"{Path(args.proxy_log)} forwards={len(forwards)} subagent_seen={seen_subagent} main_seen={seen_main}")


def cmd_gateway(args: argparse.Namespace) -> None:
    records = iter_jsonl(Path(args.gateway_log))
    errors: list[str] = []
    controlled_timeouts = 0
    parsed_success = 0
    for record in records:
        event = str(record.get("event") or "")
        status = record.get("status")
        if event in {
            "codex_cli_anthropic_structured_timeout_fallback",
            "codex_cli_openai_structured_timeout_fallback",
        }:
            controlled_timeouts += 1
            continue
        if event in {"codex_cli_anthropic_response", "codex_cli_openai_response"} and record.get("parsed_json_object"):
            parsed_success += 1
        if any(part in event for part in ("error", "bad_path", "timeout")):
            errors.append(json.dumps(record, ensure_ascii=False)[:500])
        if isinstance(status, int) and status >= 400:
            errors.append(json.dumps(record, ensure_ascii=False)[:500])
    if errors:
        die("gateway evidence failed: " + " | ".join(errors[:8]))
    # A controlled timeout fallback means a Codex agent was killed and served an
    # empty schema object instead of real work; treat it as fatal by default so a
    # hollow run can no longer pass. --max-controlled-timeouts is the escape hatch.
    if controlled_timeouts > args.max_controlled_timeouts:
        die(
            f"gateway had {controlled_timeouts} structured timeout fallbacks "
            f"(max {args.max_controlled_timeouts}) — agents were killed mid-work and served empty results"
        )
    if args.require_parsed_success and parsed_success < 1:
        die("gateway produced no parsed structured success (codex_cli_*_response with parsed_json_object)")
    suffix = f" controlled_timeouts={controlled_timeouts}" if controlled_timeouts else ""
    print(f"{Path(args.gateway_log)} records={len(records)} parsed_success={parsed_success}{suffix}")


def command_tokens(data: Any) -> list[str]:
    if isinstance(data, list):
        return [str(item) for item in data]
    if isinstance(data, dict):
        command = data.get("command") or data.get("cmd") or data.get("argv") or []
        if isinstance(command, list):
            return [str(item) for item in command]
        if isinstance(command, str):
            return command.split()
    return []


def cmd_codex_logs(args: argparse.Namespace) -> None:
    log_dir = Path(args.codex_log_dir)
    if not log_dir.is_dir():
        die(f"missing Codex log dir: {log_dir}")
    commands = sorted(log_dir.glob("*.command.json"))
    if not commands:
        die(f"no Codex CLI command logs found in {log_dir}")

    accepted: list[Path] = []
    for path in commands:
        data = load_json(path)
        tokens = command_tokens(data)
        joined = "\n".join(tokens)
        model = str(data.get("model") or "")
        has_model = model == args.expected_model or args.expected_model in tokens
        if (
            "exec" in tokens
            and has_model
            and 'web_search="live"' in joined
            and 'approval_policy="never"' in joined
        ):
            accepted.append(path)
    if not accepted:
        die(f"no Codex command proved exec model={args.expected_model} live-search approval-never in {log_dir}")
    print(accepted[-1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    workflow = sub.add_parser("workflow")
    workflow.add_argument("--projects-root", required=True)
    workflow.add_argument("--marker", required=True)
    workflow.add_argument("--start-ms", type=int, required=True)
    workflow.add_argument("--expected-name", default="")
    workflow.add_argument("--expected-status", default="")
    workflow.add_argument("--min-agents", type=int, default=1)
    workflow.add_argument("--require-all-codex", action="store_true")
    workflow.add_argument("--min-nonempty-agents", type=int, default=1)
    workflow.set_defaults(func=cmd_workflow)

    proxy = sub.add_parser("proxy")
    proxy.add_argument("--proxy-log", required=True)
    proxy.add_argument("--require-main", action="store_true")
    proxy.add_argument("--require-subagent", action="store_true")
    proxy.add_argument("--require-codex-alias", action="store_true")
    proxy.set_defaults(func=cmd_proxy)

    gateway = sub.add_parser("gateway")
    gateway.add_argument("--gateway-log", required=True)
    gateway.add_argument("--max-controlled-timeouts", type=int, default=0)
    gateway.add_argument("--require-parsed-success", action="store_true")
    gateway.set_defaults(func=cmd_gateway)

    codex_logs = sub.add_parser("codex-logs")
    codex_logs.add_argument("--codex-log-dir", required=True)
    codex_logs.add_argument("--expected-model", default="gpt-5.4")
    codex_logs.set_defaults(func=cmd_codex_logs)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
