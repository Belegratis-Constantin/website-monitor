"""Local HTTP server for the test suite.

Serves ``tests/fixtures/site`` (or any directory) and emulates a typical PHP
mailer at ``/api/send.php`` with common spam guards:

  method → Origin → honeypot (fake success) → time-trap → form type → required fields → deliver

Every message that *would* have been mailed is appended to
``server.deliveries`` — tests assert it stays empty.

Run standalone to try the monitor against it::

    python -m tests.fixture_server [--root DIR] [--port 8765]
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SITE_ROOT = Path(__file__).parent / "fixtures" / "site"


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path = SITE_ROOT, port: int = 0, *, spa_fallback: bool = True,
                 min_fill_seconds: int = 3, known_forms: tuple[str, ...] = ("contact", "membership", "partner")):
        super().__init__(("127.0.0.1", port), Handler)
        self.root = Path(root)
        self.spa_fallback = spa_fallback
        self.min_fill_seconds = min_fill_seconds
        self.known_forms = known_forms
        self.deliveries: list[dict[str, str]] = []
        self.mailer_calls: list[tuple[int, str]] = []  # (status, guard)
        self.mailer_status = 200  # set to 500 to simulate a failing mail provider
        self.redirects = {"/old": "/about.html", "/old.html": "/about.html"}

    def handle_error(self, request, client_address) -> None:
        # Clients (streamed existence checks) hang up early; that's expected.
        import sys
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> FixtureServer:
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class Handler(BaseHTTPRequestHandler):
    server: FixtureServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, ok: bool, message: str, guard: str) -> None:
        self.server.mailer_calls.append((status, guard))
        self._send(status, json.dumps({"ok": ok, "message": message}).encode(), "application/json; charset=utf-8")

    def _file(self, path: Path, status: int = 200) -> None:
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/xml", "image/svg+xml", "application/javascript"):
            data = data.replace(b"__BASE__", self.server.base_url.encode())
            ctype += "; charset=utf-8"
        self._send(status, data, ctype)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/send.php":
            return self._json(405, False, "Method not allowed", "method")
        if path in self.server.redirects:
            return self._send(301, b"", "text/html", {"Location": self.server.redirects[path]})
        if path in ("/.env", "/.htaccess") or path.startswith("/.git"):
            return self._send(403, b"Forbidden", "text/plain")
        target = (self.server.root / path.lstrip("/")).resolve()
        if path.endswith("/"):
            target = target / "index.html"
        if self.server.root.resolve() in target.parents and target.is_file():
            return self._file(target)
        if self.server.spa_fallback:
            return self._file(self.server.root / "index.html")  # soft 404, like many SPA hosts
        return self._send(404, b"<!doctype html><title>Not found</title><h1>Not found</h1>", "text/html")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/send.php":
            return self._send(405, b"", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode(), keep_blank_values=True).items()}
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not origin.startswith(self.server.base_url):
            return self._json(403, False, "Invalid origin", "origin")
        if form.get("website"):
            return self._json(200, True, "ok", "honeypot")
        try:
            rendered = int(form.get("ts", "0"))
        except ValueError:
            rendered = 0
        elapsed = time.time() - rendered / 1000
        if rendered <= 0 or elapsed < self.server.min_fill_seconds or elapsed > 3600:
            return self._json(422, False, "Submission rejected", "time-trap")
        if form.get("form_type") not in self.server.known_forms:
            return self._json(422, False, "Unknown form", "form_type")
        if not form.get("name") or "@" not in form.get("email", ""):
            return self._json(422, False, "Please fill in all required fields", "validation")
        if self.server.mailer_status != 200:
            return self._json(self.server.mailer_status, False, "Could not send message", "mailer")
        self.server.deliveries.append(form)
        return self._json(200, True, "ok", "delivered")


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(SITE_ROOT))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--min-fill-seconds", type=int, default=3)
    args = parser.parse_args()
    server = FixtureServer(Path(args.root), args.port, min_fill_seconds=args.min_fill_seconds)
    print(f"Serving {args.root} on {server.base_url} (deliveries are only recorded)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"Deliveries: {len(server.deliveries)}")
