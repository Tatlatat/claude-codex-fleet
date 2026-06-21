#!/usr/bin/env python3
"""Regression: ccr-proxy must forward SSE events line-by-line, not buffer them.

Root cause of "UltraCode/deep-research lanes still die at 180s even after the
gateway emits heartbeats": forward() read the upstream with `response.read(65536)`,
a fixed-size read that blocks until 64KB accumulates OR the upstream closes. The
gateway's heartbeat is a few bytes every 10s, so read(65536) held the entire
heartbeat trickle in a buffer for minutes — it never reached the Claude Code
workflow runtime, whose no-progress watchdog then killed the lane at 180s.

This test stands up a slow SSE upstream that emits small events spaced apart and
asserts they arrive at the client spread out in time (streamed), not collapsed
into a single late write (buffered).
"""
from __future__ import annotations

import http.server
import importlib.util
import io
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ccr_proxy_stream", ROOT / "ccr-claude-proxy.py")
ccr = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ccr)


def expect(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


class _Upstream(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for i in range(6):
            self.wfile.write(f"event: hb\ndata: {{\"i\":{i}}}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.3)

    def log_message(self, *a):
        pass


class _TimedWFile(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.events = []
        self.t0 = time.time()

    def write(self, b):
        self.events.append((time.time() - self.t0, b))
        return super().write(b)

    def flush(self):
        pass


def _make_handler(up_port):
    class FakeHandler(ccr.Handler):
        def __init__(self):
            self.command = "POST"
            self.path = "/v1/messages"
            self.headers = {}
            self.wfile = _TimedWFile()

        target = f"http://127.0.0.1:{up_port}/"
        main_target = target
        direct_alias_target = ""
        api_key = ""
        passthrough_main = False

        def send_response(self, c, m=None):
            pass

        def send_header(self, k, v):
            pass

        def end_headers(self):
            pass

        def trace(self, *a, **k):
            pass

        def forward_headers(self, to):
            return {}

        def route_for_body(self, b):
            return self.target, True, b

    return FakeHandler()


def test_sse_streamed_not_buffered():
    up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    up_port = up.server_address[1]
    threading.Thread(target=up.serve_forever, daemon=True).start()
    try:
        h = _make_handler(up_port)
        h.forward(b'{"model":"claude-codex-pro","stream":true,"messages":[]}')
        hb = [t for t, b in h.wfile.events if b"data:" in b]
        expect(len(hb) >= 4, f"expected >=4 heartbeat events forwarded, got {len(hb)}: {hb}")
        spread = hb[-1] - hb[0]
        # Upstream spaces events 0.3s apart over ~1.5s. Buffered delivery collapses
        # them to <0.5s spread (one late write); streamed delivery preserves it.
        expect(spread > 0.5,
               f"SSE events arrived buffered (spread {spread:.2f}s) — ccr-proxy is "
               f"holding the heartbeat trickle instead of streaming it line-by-line")
    finally:
        up.shutdown()


def main() -> int:
    test_sse_streamed_not_buffered()
    print("PASS: ccr-proxy SSE line-streaming")
    return 0


if __name__ == "__main__":
    sys.exit(main())
