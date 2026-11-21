from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from garden.collector import collect
from garden.core import Window
from garden.display import render_plain, status_line
from garden.ledger import connect, read_snapshot
from garden.service import run_service
from garden.ui import Client, run_ui

PROJECT = Path(__file__).resolve().parent.parent


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A little local Pi usage garden for Herdr")
    parser.add_argument("command", choices=("serve", "scan", "status", "show", "ui"))
    parser.add_argument("--state", type=Path, default=PROJECT / ".state")
    parser.add_argument("--sessions", type=Path, action="append")
    parser.add_argument("--port", type=int, default=9467)
    parser.add_argument("--window", choices=("1h", "24h"), default="1h")
    parser.add_argument("--width", type=int, default=shutil.get_terminal_size((76, 24)).columns)
    parser.add_argument(
        "--offline", action="store_true", help="read the local ledger instead of HTTP"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.width < 12:
        parser.error("width must be at least 12")
    return args


def main() -> int:
    os.umask(0o077)
    args = arguments()
    db_path = args.state.resolve() / "usage.sqlite3"
    roots = tuple(
        path.expanduser().resolve()
        for path in (args.sessions or (Path.home() / ".pi/agent/sessions",))
    )
    window: Window = 3600 if args.window == "1h" or args.command == "status" else 86400
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    match args.command:
        case "serve":
            run_service(db_path, roots, args.port)
        case "scan":
            with closing(connect(db_path)) as db:
                result = collect(db, roots, time.time())
            logging.info("Indexed %s session files (%s changed)", result.files, result.changed)
        case "ui":
            run_ui(args.port)
        case "status" | "show":
            try:
                data = (
                    read_snapshot(db_path, time.time(), window)
                    if args.offline
                    else Client(args.port).fetch(window)
                )
            except (sqlite3.Error, OSError):
                data = None
            output = (
                status_line(data, args.width)
                if args.command == "status"
                else render_plain(data, args.width)
            )
            sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
