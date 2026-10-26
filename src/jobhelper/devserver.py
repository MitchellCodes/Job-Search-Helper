"""Dev/browser mode: serves the screens over http://127.0.0.1 and forwards /api/<name> calls.

Used to test the interface in an ordinary browser (and in automated checks) without the
native window. It listens on the loopback address only. The native open/save dialogs are
unavailable in this mode: use `upload_resume_path`, and exports go to the data folder's exports/.
"""
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jobhelper.api import PUBLIC_METHODS, Api

WEB_DIR = Path(__file__).resolve().parent / "web"


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            rel = self.path.split("?", 1)[0].lstrip("/") or "index.html"
            target = (WEB_DIR / rel).resolve()
            if WEB_DIR not in target.parents or not target.is_file():
                return self._send(404, b"Not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), f"{ctype}; charset=utf-8" if ctype.startswith("text") or ctype.endswith("javascript") else ctype)

        def do_POST(self):
            name = self.path.removeprefix("/api/")
            if not self.path.startswith("/api/") or name not in PUBLIC_METHODS:
                return self._send(404, b'{"ok":false,"error":"unknown method"}', "application/json")
            length = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(length) or b"[]")
            result = getattr(api, name)(*args)
            self._send(200, json.dumps(result).encode("utf-8"), "application/json")

    return Handler


def serve(api: Api, port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    print(f"Dev server on http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
