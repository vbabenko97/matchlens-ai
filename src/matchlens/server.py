"""Static-site export and a tiny local server for the replay UI (stdlib only)."""

from __future__ import annotations

import http.server
import shutil
from importlib import resources
from pathlib import Path

from .replay import dumps

WEB_FILES = ("index.html", "app.js", "style.css")
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _web_dir() -> Path:
    return Path(str(resources.files("matchlens") / "web"))


def site_files(bundle: dict) -> dict[str, bytes]:
    """Every file of the static site, keyed by URL path (without the leading slash)."""
    files = {name: (_web_dir() / name).read_bytes() for name in WEB_FILES}
    files["data/bundle.json"] = dumps(bundle).encode()
    files["data/overlays.json"] = dumps(bundle["overlays"]).encode()
    return files


def export_site(bundle: dict, out: Path) -> list[Path]:
    if out.exists():
        shutil.rmtree(out)
    written = []
    for rel, data in site_files(bundle).items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)
    return written


def make_handler(bundle: dict) -> type[http.server.BaseHTTPRequestHandler]:
    files = site_files(bundle)
    match_id = bundle["meta"]["match_id"]

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "MatchLens"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            path = self.path.split("?", 1)[0].lstrip("/") or "index.html"
            data = files.get(path)  # only known files: no filesystem access, no traversal
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", _TYPES[Path(path).suffix])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:",
            )
            if path == "data/overlays.json":
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="matchlens-overlays-{match_id}.json"',
                )
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

    return Handler


def make_server(bundle: dict, host: str = "127.0.0.1", port: int = 8000):
    return http.server.ThreadingHTTPServer((host, port), make_handler(bundle))
