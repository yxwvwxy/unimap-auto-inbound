from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from .hub import InboundHub

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18787
ALLOWED_ORIGINS = (
    "https://us.si.uniuni.com",
    "http://127.0.0.1:18787",
    "http://localhost:18787",
)


def _cors_origin(origin: str) -> Optional[str]:
    if origin in ALLOWED_ORIGINS:
        return origin
    return None


class HubHandler(BaseHTTPRequestHandler):
    hub: InboundHub

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        print(f"[local-api] {self.address_string()} - {format % args}")

    def _origin(self) -> str:
        return self.headers.get("Origin", "")

    def _send(self, code: int, body: dict, origin: str = "") -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        allow = _cors_origin(origin or self._origin())
        if allow:
            self.send_header("Access-Control-Allow-Origin", allow)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        allow = _cors_origin(self._origin())
        if allow:
            self.send_header("Access-Control-Allow-Origin", allow)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/health"}:
            self._send(200, {"ok": True, "watching": True, "service": "unimap-auto-inbound"})
            return
        if path == "/status":
            self._send(200, self.hub.snapshot())
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"ok": False, "error": "invalid JSON"})
            return
        if not isinstance(data, dict):
            data = {}

        if path == "/queue":
            orders = data.get("orders") or []
            if isinstance(orders, str):
                orders = [line.strip() for line in orders.replace(",", "\n").splitlines()]
            if not isinstance(orders, list):
                self._send(400, {"ok": False, "error": "orders must be a list"})
                return
            result = self.hub.enqueue(orders, force=bool(data.get("force")))
            self._send(200 if result.get("ok") else 409, result)
            return

        if path == "/stop":
            result = self.hub.request_stop()
            self._send(200 if result.get("ok") else 409, result)
            return

        self._send(404, {"ok": False, "error": "not found"})


def start_local_api(
    hub: InboundHub,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> ThreadingHTTPServer:
    handler = type("BoundHubHandler", (HubHandler,), {"hub": hub})
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, name="inbound-local-api", daemon=True)
    thread.start()
    print(f"Local trigger listening on http://{host}:{port}")
    print("SI 页面脚本会把单号 POST 到这里；真正点 UniMap 仍在本机浏览器。")
    return server
