#!/usr/bin/env python3
"""Regression tests for ccr-claude-proxy.py forward() timeout + error handling.

Reproduces the router-mode failure where a slow Codex subagent produced
`subagent_tokens: 0` and hung for ~1 hour:

  1. forward() called urllib.urlopen(..., timeout=3600) -> a wedged upstream
     blocks the proxy for a full hour before anything reaches Claude Code.
  2. A socket/URL timeout (upstream wedged, no HTTP status) fell through to the
     generic `except Exception` -> bare 502 with no Anthropic-shaped body, so
     the subagent ended with zero content and no actionable signal.
  3. An upstream HTTPError 504 (gateway codex_timeout) must be forwarded as a
     clean 504 the client can interpret, not silently dropped.

The tests drive forward() directly against a fake handler with urlopen
monkeypatched, so no sockets or network are involved.
"""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import socket
import sys
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
PROXY_PATH = ROOT / "ccr-claude-proxy.py"

spec = importlib.util.spec_from_file_location("ccr_claude_proxy", PROXY_PATH)
proxy = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(proxy)


class FakeWFile(io.BytesIO):
    def flush(self) -> None:  # BytesIO.flush is a no-op; keep it explicit
        pass


class FakeHandler(proxy.Handler):
    """A Handler with the HTTP machinery stubbed so forward() runs in-process."""

    def __init__(self) -> None:  # noqa: D401 - bypass BaseHTTPRequestHandler.__init__
        self.command = "POST"
        self.path = "/v1/messages"
        self.headers = {}
        self.wfile = FakeWFile()
        self.status_line = None
        self.sent_headers: dict[str, str] = {}
        self.ended = False

    # target/passthrough_main/etc. are read-only @property on Handler (they read
    # self.server); override them as plain class attributes for the fake.
    target = "http://127.0.0.1:1/"
    main_target = "http://127.0.0.1:1/"
    direct_alias_target = ""
    api_key = ""
    passthrough_main = False

    # --- stub the BaseHTTPRequestHandler response surface ---
    def send_response(self, code, message=None):  # type: ignore[override]
        self.status_line = code

    def send_header(self, key, value):  # type: ignore[override]
        self.sent_headers[key.lower()] = value

    def end_headers(self):  # type: ignore[override]
        self.ended = True

    def trace(self, event, **fields):  # type: ignore[override]
        pass

    def forward_headers(self, to_ccr):  # type: ignore[override]
        return {}

    def route_for_body(self, body):  # type: ignore[override]
        return self.target, True, body

    @property
    def body_text(self) -> str:
        return self.wfile.getvalue().decode("utf-8", "replace")


def make_urlopen(captured, *, raise_exc=None, status=200, payload=b"ok"):
    def _urlopen(req, timeout=None):
        captured["timeout"] = timeout
        if raise_exc is not None:
            raise raise_exc
        return _FakeResponse(status, payload)
    return _urlopen


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
        self.headers = _Headers({"content-type": "application/json"})

    def read(self, n=-1):
        if not self._payload:
            return b""
        chunk, self._payload = self._payload, b""
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Headers(dict):
    def items(self):
        return list(super().items())


def expect(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def test_timeout_is_bounded():
    """forward() must not block on a wedged upstream for an hour."""
    captured = {}
    orig = proxy.urllib.request.urlopen
    proxy.urllib.request.urlopen = make_urlopen(captured)
    try:
        h = FakeHandler()
        h.forward(b'{"model":"claude-codex-pro"}')
    finally:
        proxy.urllib.request.urlopen = orig
    t = captured.get("timeout")
    expect(t is not None, "urlopen called without an explicit timeout")
    expect(t <= 660, f"forward() timeout={t}s is too high (was 3600); must be <= 660s")


def test_socket_timeout_becomes_504_with_body():
    """A wedged upstream (socket timeout, no HTTP status) must surface as a
    504 with an Anthropic-shaped error body, never a hang or an empty 502."""
    captured = {}
    orig = proxy.urllib.request.urlopen
    proxy.urllib.request.urlopen = make_urlopen(
        captured, raise_exc=urllib.error.URLError(socket.timeout("timed out"))
    )
    try:
        h = FakeHandler()
        h.forward(b'{"model":"claude-codex-pro"}')
    finally:
        proxy.urllib.request.urlopen = orig
    expect(h.status_line == 504, f"socket timeout -> status {h.status_line}, want 504")
    expect(h.ended, "response headers were never ended on timeout")
    body = h.body_text
    expect('"error"' in body, f"504 body missing Anthropic error shape: {body!r}")
    expect("timeout" in body.lower(), f"504 body should name the timeout cause: {body!r}")


def test_upstream_504_forwarded():
    """An upstream HTTPError 504 (gateway codex_timeout) must pass through."""
    captured = {}
    hdrs = _Headers({"content-type": "application/json"})
    err = urllib.error.HTTPError(
        "http://x/v1/messages", 504, "Gateway Timeout", hdrs,
        io.BytesIO(b'{"type":"error","error":{"type":"codex_timeout"}}'),
    )
    orig = proxy.urllib.request.urlopen
    proxy.urllib.request.urlopen = make_urlopen(captured, raise_exc=err)
    try:
        h = FakeHandler()
        h.forward(b'{"model":"claude-codex-pro"}')
    finally:
        proxy.urllib.request.urlopen = orig
    expect(h.status_line == 504, f"upstream 504 -> status {h.status_line}, want 504")
    expect("codex_timeout" in h.body_text, f"504 body not forwarded: {h.body_text!r}")


def main() -> int:
    test_timeout_is_bounded()
    test_socket_timeout_becomes_504_with_body()
    test_upstream_504_forwarded()
    print("PASS: ccr-proxy timeout + error handling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
