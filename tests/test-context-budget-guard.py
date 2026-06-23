from __future__ import annotations
import importlib.util, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("gw", ROOT / "codex-native-gateway.py")
gw = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(gw)


def expect(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def test_directive_names_concrete_caps():
    os.environ.pop("CLAUDE_CODEX_GATEWAY_CONTEXT_GUARD", None)
    os.environ.pop("CLAUDE_CODEX_GATEWAY_MAX_FILE_READS", None)
    d = gw.context_budget_directive()
    # The guard must give the lane a concrete read budget and tell it to summarize
    # rather than dump whole files — that is what stops the 833-read / 532K-token blowup.
    expect("file" in d.lower(), "directive mentions files")
    expect(any(c.isdigit() for c in d), "directive states a concrete number")
    expect("summar" in d.lower() or "tóm" in d.lower(), "directive tells the lane to summarize, not dump")


def test_max_file_reads_env_appears_in_directive():
    os.environ["CLAUDE_CODEX_GATEWAY_MAX_FILE_READS"] = "40"
    try:
        d = gw.context_budget_directive()
        expect("40" in d, "configured read cap (40) is surfaced in the directive")
    finally:
        os.environ.pop("CLAUDE_CODEX_GATEWAY_MAX_FILE_READS", None)


def test_disabled_returns_empty():
    os.environ["CLAUDE_CODEX_GATEWAY_CONTEXT_GUARD"] = "0"
    try:
        expect(gw.context_budget_directive() == "", "kill-switch disables the guard")
    finally:
        os.environ.pop("CLAUDE_CODEX_GATEWAY_CONTEXT_GUARD", None)


def test_guard_is_prefix_stable():
    # The guard text must be identical across calls (lane-invariant) so it can sit at
    # the FRONT of the prompt without breaking the shared DeepSeek prefix.
    os.environ.pop("CLAUDE_CODEX_GATEWAY_CONTEXT_GUARD", None)
    os.environ.pop("CLAUDE_CODEX_GATEWAY_MAX_FILE_READS", None)
    a = gw.context_budget_directive()
    b = gw.context_budget_directive()
    expect(a == b, "guard directive is byte-identical across calls")
    expect(a != "", "guard is on by default")


def test_guard_present_in_assembled_prompt_for_tool_lane():
    # A lane that has tools (can read files) should carry the guard near the front.
    msgs = [{"role": "user", "content": "Read the whole repo and report."}]
    tools = [{"name": "read_file", "input_schema": {"type": "object"}}]
    out = gw.openai_messages_to_prompt(msgs, tools)
    expect("file" in out.lower() and any(c.isdigit() for c in out),
           "assembled tool-lane prompt carries the context-budget guard")


if __name__ == "__main__":
    test_directive_names_concrete_caps()
    test_max_file_reads_env_appears_in_directive()
    test_disabled_returns_empty()
    test_guard_is_prefix_stable()
    test_guard_present_in_assembled_prompt_for_tool_lane()
    print("PASS: context budget guard")
