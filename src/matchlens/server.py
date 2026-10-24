"""Static-site export and a tiny local server for the replay UI (stdlib only)."""

from __future__ import annotations

import hashlib
import http.server
import json
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from .live import LIVE_PATH, MAX_BODY_BYTES, LiveChecker, handle
from .replay import BUNDLE_SCHEMA, dumps, match_entry

WEB_FILES = ("index.html", "app.js", "style.css", "broadcast.html", "broadcast.js", "broadcast.css")
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _web_dir() -> Path:
    return Path(str(resources.files("matchlens") / "web"))


# Static hosts (GitHub Pages: max-age=600) cache assets; a stale style.css or app.js next to
# a new index.html breaks the page. Each reference carries a hash of the file it loads.
VERSIONED_REFS = {
    "index.html": {"style.css": b'href="style.css"', "app.js": b'src="app.js"'},
    "broadcast.html": {
        "broadcast.css": b'href="broadcast.css"',
        "broadcast.js": b'src="broadcast.js"',
    },
}


def asset_version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _versioned_page(files: dict[str, bytes], page: str) -> bytes:
    html = files[page]
    for name, ref in VERSIONED_REFS[page].items():
        if html.count(ref) != 1:
            raise ValueError(f"{page} must reference {name} exactly once as {ref.decode()}")
        tagged = ref[:-1] + b"?v=" + asset_version(files[name]).encode() + b'"'
        html = html.replace(ref, tagged)
    return html


MATCHES_SCHEMA = "matchlens.matches/v1"


def site_files(bundle: dict, extras: Sequence[dict] = ()) -> dict[str, bytes]:
    """Every file of the static site, keyed by URL path (without the leading slash).

    ``bundle`` is the default match (``data/bundle.json``); ``extras`` are more matches for the
    picker, each as ``data/matches/<match_id>.json`` plus its overlay cues. ``data/matches.json``
    lists them all, the default first.
    """
    files = {name: (_web_dir() / name).read_bytes() for name in WEB_FILES}
    for page in VERSIONED_REFS:
        files[page] = _versioned_page(files, page)
    files["data/bundle.json"] = dumps(bundle).encode()
    files["data/overlays.json"] = dumps(bundle["overlays"]).encode()
    entries = [match_entry(bundle, "data/bundle.json", "data/overlays.json")]
    for extra in extras:
        base = f"data/matches/{extra['meta']['match_id']}"
        if f"{base}.json" in files or extra["meta"]["match_id"] == bundle["meta"]["match_id"]:
            raise ValueError(f"duplicate match {extra['meta']['match_id']}")
        files[f"{base}.json"] = dumps(extra).encode()
        files[f"{base}-overlays.json"] = dumps(extra["overlays"]).encode()
        entries.append(match_entry(extra, f"{base}.json", f"{base}-overlays.json"))
    files["data/matches.json"] = dumps({"schema": MATCHES_SCHEMA, "matches": entries}).encode()
    return files


def _is_previous_export(out: Path) -> bool:
    try:
        doc = json.loads((out / "data" / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("schema") == BUNDLE_SCHEMA


def export_site(bundle: dict, out: Path, extras: Sequence[dict] = ()) -> list[Path]:
    """Write the static site into ``out``. Never deletes anything.

    ``out`` must be missing, empty, or a previous MatchLens export (files are overwritten
    in place); any other non-empty directory is refused so user files cannot be clobbered.
    """
    if out.exists() and (not out.is_dir() or (any(out.iterdir()) and not _is_previous_export(out))):
        raise FileExistsError(f"{out} exists and is not an empty directory or a MatchLens export")
    written = []
    for rel, data in site_files(bundle, extras).items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)
    return written


def make_handler(
    bundle: dict, checker: LiveChecker | None = None, extras: Sequence[dict] = ()
) -> type[http.server.BaseHTTPRequestHandler]:
    """Static files, plus ``POST /api/live-check`` when a live checker is given."""
    files = site_files(bundle, extras)
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
            if path.startswith("data/") and path.endswith("overlays.json"):
                name = Path(path).name.removesuffix("overlays.json").rstrip("-") or match_id
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="matchlens-overlays-{name}.json"',
                )
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            if checker is None or self.path.split("?", 1)[0].lstrip("/") != LIVE_PATH:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, MAX_BODY_BYTES + 1))
            status, reply = handle(
                checker, "POST", dict(self.headers), body, self.client_address[0]
            )
            data = dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", _TYPES[".json"])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

    return Handler


def make_server(
    bundle: dict,
    host: str = "127.0.0.1",
    port: int = 8000,
    checker: LiveChecker | None = None,
    extras: Sequence[dict] = (),
):
    return http.server.ThreadingHTTPServer((host, port), make_handler(bundle, checker, extras))
