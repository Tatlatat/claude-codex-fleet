#!/usr/bin/env python3
"""The reasonix-fleet MCP must run the REASONIX fork engine (not the legacy CLI)
when CLAUDE_REASONIX_FLAVOR=reasonix.

Root cause this guards: in a claude-reasonix session, single subagents are pushed
to the reasonix_fleet MCP by only-reasonix-fleet.py; the MCP must dispatch through
the gateway's reasonix engine path (now the in-process fork-engine shim), not a
legacy subprocess.

The MCP's per-task runner dispatches through the gateway's run_reasonix_acp, which
now spawns the one-shot fork-engine shim (`node engine/run-lane.mjs`). This test
drives that shim in REASONIX_ENGINE_MOCK mode (deterministic text/usage, no
DeepSeek) and asserts the task result came from the reasonix engine (cost +
text surfaced), exactly as before — only the engine handle changed.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("reasonix_fleet_mcp", ROOT / "reasonix-fleet-mcp.py")
mcp = importlib.util.module_from_spec(spec)
assert spec and spec.loader


def expect(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def test_mcp_runs_reasonix_in_reasonix_flavor():
    os.environ["CLAUDE_REASONIX_FLAVOR"] = "reasonix"
    # Drive the fork-engine shim in mock mode with injected values.
    mock_env = {
        "REASONIX_ENGINE_MOCK": "1",
        "REASONIX_ENGINE_MOCK_TEXT": "REASONIX_RAN",
        "REASONIX_ENGINE_MOCK_COST": "0.000222",
        "REASONIX_ENGINE_MOCK_PROMPT_TOKENS": "50",
        "REASONIX_ENGINE_MOCK_COMPLETION_TOKENS": "3",
        "REASONIX_ENGINE_MOCK_CACHE_HIT_TOKENS": "45",
        "REASONIX_ENGINE_MOCK_CACHE_MISS_TOKENS": "5",
    }
    saved = {k: os.environ.get(k) for k in mock_env}
    os.environ.update(mock_env)
    try:
        spec.loader.exec_module(mcp)  # load with reasonix env set
        cwd = tempfile.mkdtemp()
        task = {"title": "t", "prompt": "say OK", "cwd": cwd}
        result = asyncio.run(mcp.run_one_task(task, 0, "batch-test", 8000))
        expect(result.get("ok") is True, f"task should succeed: {result}")
        out = str(result.get("output") or result.get("stdout") or "")
        expect("REASONIX_RAN" in out, f"output must come from reasonix engine: {result}")
        # cost from the reasonix engine must be surfaced
        expect(result.get("reasonix_cost_usd") == 0.000222,
               f"reasonix cost must be captured: {result}")
    finally:
        os.environ.pop("CLAUDE_REASONIX_FLAVOR", None)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main() -> int:
    test_mcp_runs_reasonix_in_reasonix_flavor()
    print("PASS: mcp reasonix flavor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
