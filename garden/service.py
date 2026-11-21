from __future__ import annotations

import json
import logging
import signal
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Thread
from types import FrameType
from urllib.parse import parse_qs, urlsplit

from garden.core import Snapshot, Window
from garden.runtime import CollectorCache, sample_tokens

LOGGER = logging.getLogger(__name__)
CATEGORIES = ("input", "output", "cache_read", "cache_write")


def metrics(snapshots: tuple[Snapshot, ...]) -> str:
    lines = [
        "# HELP pi_token_window_tokens Observed Pi tokens in a trailing wall-clock window.",
        "# TYPE pi_token_window_tokens gauge",
    ]
    for data in snapshots:
        for row in data.rows:
            labels = (
                f"provider={json.dumps(row.provider, ensure_ascii=False)},"
                f"model={json.dumps(row.model, ensure_ascii=False)}"
            )
            for category in CATEGORIES:
                value = row.usage.model_dump()[category]
                lines.append(
                    f'pi_token_window_tokens{{{labels},kind="{category}",'
                    f'window="{data.window_seconds}s"}} {value}'
                )
    if snapshots:
        data = snapshots[0]
        lines.extend(
            (
                "# HELP pi_garden_last_scan_timestamp_seconds Last completed collector scan.",
                "# TYPE pi_garden_last_scan_timestamp_seconds gauge",
                f"pi_garden_last_scan_timestamp_seconds {data.updated_at}",
                "# HELP pi_garden_coverage_issues Current ingestion coverage issues.",
                "# TYPE pi_garden_coverage_issues gauge",
                f"pi_garden_coverage_issues {data.warnings}",
                "# HELP pi_garden_session_files Number of discovered saved session files.",
                "# TYPE pi_garden_session_files gauge",
                f"pi_garden_session_files {data.session_files}",
            )
        )
    return "\n".join(lines) + "\n"


def handler(cache: CollectorCache) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, format: str, *args: object) -> None:
            pass

        def respond(self, code: int, content: str, content_type: str) -> None:
            body = content.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self) -> bool:
            if not isinstance(self.server, HTTPServer):
                return False
            port = self.server.server_port
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_POST(self) -> None:
            self.respond(
                404 if self.allowed() else 403,
                "not found\n",
                "text/plain",
            )

        def do_GET(self) -> None:
            if not self.allowed():
                self.respond(403, "localhost only\n", "text/plain")
                return
            url = urlsplit(self.path)
            now = time.time()
            cached = cache.read(now)
            if url.path == "/metrics":
                self.respond(
                    200,
                    metrics(cached.tokens),
                    "text/plain; version=0.0.4; charset=utf-8",
                )
            elif url.path == "/usage":
                query = parse_qs(url.query)
                window: Window
                match query.get("window", ["3600"]):
                    case ["3600"]:
                        window = 3600
                    case ["86400"]:
                        window = 86400
                    case _:
                        self.respond(400, "window must be 3600 or 86400\n", "text/plain")
                        return
                tokens = cached.token(now, window)
                if tokens is None:
                    self.respond(503, "token sample unavailable\n", "text/plain")
                else:
                    self.respond(200, tokens.model_dump_json(), "application/json")
            else:
                self.respond(404, "not found\n", "text/plain")

    return Handler


def serve(db_path: Path, roots: tuple[Path, ...], port: int, stop: Event) -> None:
    cache = CollectorCache()
    worker = Thread(target=sample_tokens, args=(db_path, roots, cache, stop), name="garden-tokens")
    with HTTPServer(("127.0.0.1", port), handler(cache)) as server:
        server.timeout = 0.5
        LOGGER.info("Garden listening on http://127.0.0.1:%s", server.server_port)
        try:
            worker.start()
            while not stop.is_set():
                server.handle_request()
        finally:
            stop.set()
            if worker.ident is not None:
                worker.join()


def run_service(db_path: Path, roots: tuple[Path, ...], port: int) -> None:
    stop = Event()

    def stopping(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, stopping)
    signal.signal(signal.SIGTERM, stopping)
    serve(db_path, roots, port, stop)
