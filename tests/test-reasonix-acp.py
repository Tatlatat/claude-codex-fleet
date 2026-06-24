#!/usr/bin/env python3
"""Unit tests for run_reasonix_acp driving the in-process fork engine SHIM.

The gateway no longer spawns upstream `reasonix acp`; run_reasonix_acp now spawns
`node engine/run-lane.mjs` (the one-shot fork-engine shim) and reads its one-line
JSON. These tests drive that shim in REASONIX_ENGINE_MOCK mode — which returns a
deterministic {text, usage, cost} WITHOUT DeepSeek — with mock values injected via
env, so the assertions (real-ish text + non-zero cost/cache/token counts) are the
same contract the old fake-`reasonix` driver verified. Only the engine handle
changed (acp subprocess -> node shim).
"""
from __future__ import annotations
import importlib.util, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rx_gateway", ROOT / "reasonix-native-gateway.py")
gw = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(gw)

def expect(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")

def _with_shim_mock(**overrides):
    """Context: set REASONIX_ENGINE_MOCK + value overrides so run_reasonix_acp's
    shim spawn returns deterministic text/usage. Returns a dict of saved env to
    restore."""
    env = {
        "REASONIX_ENGINE_MOCK": "1",
        "REASONIX_ENGINE_MOCK_TEXT": overrides.get("text", "PONG"),
        "REASONIX_ENGINE_MOCK_COST": str(overrides.get("cost", 0.000123)),
        "REASONIX_ENGINE_MOCK_PROMPT_TOKENS": str(overrides.get("prompt_tokens", 100)),
        "REASONIX_ENGINE_MOCK_COMPLETION_TOKENS": str(overrides.get("completion_tokens", 4)),
        "REASONIX_ENGINE_MOCK_CACHE_HIT_TOKENS": str(overrides.get("hit", 90)),
        "REASONIX_ENGINE_MOCK_CACHE_MISS_TOKENS": str(overrides.get("miss", 10)),
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    return saved

def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

def test_accumulates_text_and_cost():
    saved = _with_shim_mock(text="PONG", cost=0.000123, prompt_tokens=100,
                            completion_tokens=4, hit=90, miss=10)
    try:
        cfg = {"target_model": "deepseek-v4-flash"}
        text, usage = gw.run_reasonix_acp("say PONG", cfg)
    finally:
        _restore(saved)
    expect(text == "PONG", f"expected accumulated 'PONG', got {text!r}")
    # cost + cache come from the shim's JSON (mapped from the fork TurnStats).
    expect(usage.get("reasonix_cost_usd") == 0.000123, f"cost not captured from shim: {usage}")
    expect(usage.get("reasonix_cache_pct") == 90.0, f"cache pct (90 hit / 10 miss = 90%) not computed: {usage}")
    expect(usage.get("cache_pct") == 90.0, f"cache_pct ledger key must mirror reasonix_cache_pct: {usage}")
    # token counts come from the shim usage, not an estimate.
    expect(usage.get("input_tokens") == 100, f"input_tokens should be the shim value: {usage}")
    expect(usage.get("output_tokens") == 4, f"output_tokens should be the shim value: {usage}")

def test_spawn_failure_raises_gatewayerror():
    # Point the engine dist at a path that does not exist and turn OFF mock so the
    # shim actually tries to import it and exits non-zero -> GatewayError.
    saved_dist = os.environ.get("REASONIX_ENGINE_DIST")
    saved_mock = os.environ.get("REASONIX_ENGINE_MOCK")
    os.environ["REASONIX_ENGINE_DIST"] = "/nonexistent/reasonix-engine-xyz/dist/index.js"
    os.environ.pop("REASONIX_ENGINE_MOCK", None)
    try:
        cfg = {"target_model": "deepseek-v4-flash"}
        gw.run_reasonix_acp("hi", cfg)
    except gw.GatewayError as e:
        expect(e.error_type == "reasonix_acp_error",
               f"wrong error_type: {e.error_type!r}")
        return
    except Exception as e:
        raise SystemExit(f"FAIL: expected GatewayError, got {type(e).__name__}: {e}")
    finally:
        if saved_dist is None:
            os.environ.pop("REASONIX_ENGINE_DIST", None)
        else:
            os.environ["REASONIX_ENGINE_DIST"] = saved_dist
        if saved_mock is not None:
            os.environ["REASONIX_ENGINE_MOCK"] = saved_mock
    raise SystemExit("FAIL: no exception raised — expected GatewayError")

def test_registry_has_reasonix_flash():
    os.environ["CLAUDE_REASONIX_FLAVOR"] = "reasonix"
    try:
        reg = gw.model_registry()
        expect("claude-reasonix-flash" in reg, f"registry missing claude-reasonix-flash: {list(reg)}")
        cfg = reg["claude-reasonix-flash"]
        expect(cfg.get("provider") == "reasonix_cli", f"wrong provider: {cfg}")
        expect(cfg.get("target_model") == "deepseek-v4-flash", f"wrong model: {cfg}")
    finally:
        os.environ.pop("CLAUDE_REASONIX_FLAVOR", None)

def main():
    test_accumulates_text_and_cost()
    test_spawn_failure_raises_gatewayerror()
    test_registry_has_reasonix_flash()
    print("PASS: reasonix acp driver")
    return 0

if __name__ == "__main__":
    sys.exit(main())
