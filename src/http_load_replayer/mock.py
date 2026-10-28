"""A deterministic HTTP target for the bundled experiment.

Every endpoint has a fixed think time, and two endpoints fail on a fixed schedule (every 7th
search is rejected, every 11th checkout explodes). That makes a replay run reproducible enough
to compare concurrency levels, which a random failure generator is not.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENDPOINTS: dict[str, tuple[float, int]] = {
    "/healthz": (0.001, 200),
    "/api/catalog": (0.012, 200),
    "/api/orders": (0.025, 201),
    "/api/search": (0.040, 200),
    "/api/checkout": (0.060, 200),
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockTarget/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._respond()

    def do_POST(self) -> None:  # noqa: N802
        self._respond()

    def do_PUT(self) -> None:  # noqa: N802
        self._respond()

    def do_DELETE(self) -> None:  # noqa: N802
        self._respond()

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        latency, status = ENDPOINTS.get(path, (0.0, 404))
        if path not in ENDPOINTS:
            self._send(404, {"error": "no such endpoint", "path": path}, 0.0)
            return
        counter = self.server.counters.bump(path)  # type: ignore[attr-defined]
        if path == "/api/search" and counter % 7 == 0:
            status = 429
        elif path == "/api/checkout" and counter % 11 == 0:
            status = 500
        time.sleep(latency)
        self._send(status, {"endpoint": path, "call": counter}, latency)

    def _send(self, status: int, payload: dict, latency: float) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mock-Latency-Ms", f"{latency * 1000:.1f}")
        self.end_headers()
        self.wfile.write(body)


class _Counters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, int] = {}

    def bump(self, path: str) -> int:
        with self._lock:
            self._values[path] = self._values.get(path, 0) + 1
            return self._values[path]

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)


class MockTarget:
    """A loopback server that behaves like a small, slightly unreliable API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = ThreadingHTTPServer((host, port), _Handler)
        self._server.daemon_threads = True
        self._server.request_queue_size = 128
        self._server.counters = _Counters()  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> str:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def counters(self) -> dict[str, int]:
        return self._server.counters.snapshot()  # type: ignore[attr-defined]

    def __enter__(self) -> MockTarget:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
