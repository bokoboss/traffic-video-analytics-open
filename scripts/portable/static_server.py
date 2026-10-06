"""Loopback-only static server for the extracted frontend build.

The portable package does not require Node.js at runtime.  This server serves
only the already-built ``frontend/dist`` tree and implements the small SPA
fallback required by the frontend router.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import signal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


HEALTH_PATH = "/__tva_static_health"


class _StaticHandler(BaseHTTPRequestHandler):
    server_version = "TVAStatic/1"
    protocol_version = "HTTP/1.1"

    def _server_root(self) -> Path:
        return self.server.static_root  # type: ignore[attr-defined]

    def _send_bytes(self, payload: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header(
            "Cache-Control", "no-cache" if self.path == "/" else "public, max-age=3600"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_error(self, status: int) -> None:
        payload = f"{status}\n".encode("ascii")
        self._send_bytes(payload, "text/plain; charset=utf-8", status)

    def _request_path(self) -> str | None:
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0].strip().strip("[]")
        if hostname not in {"127.0.0.1", "localhost"}:
            return None
        parsed = urlsplit(self.path)
        decoded = unquote(parsed.path)
        if not decoded.startswith("/") or "\x00" in decoded:
            return None
        return decoded

    def _file_for_request(self, request_path: str) -> Path | None:
        root = self._server_root().resolve()
        if request_path.startswith("/__tva_"):
            return None
        relative = request_path.lstrip("/")
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            return None
        if request_path == "/":
            index = (root / "index.html").resolve()
            return index if index.is_file() and root in index.parents else None
        if candidate.is_dir():
            return None
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
        # No directory listings and no implicit index.html below root.  A
        # route without a file extension is handled by the SPA fallback.
        if request_path != "/" and Path(relative).name and "." not in Path(relative).name:
            index = (root / "index.html").resolve()
            if index.is_file() and root in index.parents:
                return index
        return None

    def _handle(self) -> None:
        request_path = self._request_path()
        if request_path is None:
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        if request_path == HEALTH_PATH:
            payload = json.dumps({"status": "ok", "service": "tva-static"}).encode("utf-8")
            self._send_bytes(payload, "application/json; charset=utf-8")
            return
        file_path = self._file_for_request(request_path)
        if file_path is None:
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = file_path.read_bytes()
        except OSError:
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if file_path.suffix.lower() == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif file_path.suffix.lower() == ".css":
            content_type = "text/css; charset=utf-8"
        self._send_bytes(payload, content_type)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        self._handle()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def log_message(self, format: str, *args: object) -> None:
        # Keep normal requests out of the desktop console.  Errors remain
        # visible in the launcher-owned stderr log through the parent process.
        if args and str(args[0]).startswith("4"):
            super().log_message(format, *args)


class StaticServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, static_root: Path) -> None:
        if host != "127.0.0.1":
            raise ValueError("portable static server must bind to 127.0.0.1")
        root = static_root.expanduser().resolve()
        if not root.is_dir() or not (root / "index.html").is_file():
            raise FileNotFoundError(f"frontend_dist_missing:{root}")
        self.static_root = root
        super().__init__((host, int(port)), _StaticHandler)


def serve(static_root: Path, port: int) -> None:
    server = StaticServer("127.0.0.1", port, static_root)

    def shutdown(_signum: int, _frame: object) -> None:
        server.shutdown()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, shutdown)
        except (OSError, ValueError):
            pass
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Loopback-only static server for a TVA portable package."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5174)
    args = parser.parse_args()
    serve(args.root, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
