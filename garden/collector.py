from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from garden.core import Event, parse_line
from garden.ledger import checkpoint, finish_scan, insert_events, previous_signature

MAX_LINE_BYTES = 16 * 1024 * 1024
BATCH_SIZE = 256


@dataclass(frozen=True)
class ScanResult:
    files: int
    changed: int
    issues: int


def signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"


def discover(roots: tuple[Path, ...]) -> tuple[tuple[Path, ...], int]:
    files: set[Path] = set()
    issues: list[OSError] = []
    for root in roots:
        if not root.is_dir():
            issues.append(OSError("missing root"))
            continue
        for directory, _, names in os.walk(root, onerror=issues.append, followlinks=False):
            files.update(Path(directory, name) for name in names if name.endswith(".jsonl"))
    return tuple(sorted(files)), len(issues)


def read_file(db: sqlite3.Connection, stream: BinaryIO, now: float) -> tuple[int, bool]:
    issues = 0
    supported: bool | None = None
    batch: list[Event] = []
    while line := stream.readline(MAX_LINE_BYTES + 1):
        if len(line) > MAX_LINE_BYTES:
            while line and not line.endswith(b"\n"):
                line = stream.readline(MAX_LINE_BYTES + 1)
            issues += 1
            continue
        if not line.endswith(b"\n"):
            insert_events(db, tuple(batch), now)
            return issues, True
        parsed = parse_line(line)
        if supported is None:
            supported = (
                parsed.entry is not None
                and parsed.entry.type == "session"
                and parsed.entry.version in (2, 3)
            )
            if not supported:
                return issues + 1, False
            continue
        if parsed.issue:
            issues += 1
        elif parsed.event:
            batch.append(parsed.event)
            if len(batch) >= BATCH_SIZE:
                insert_events(db, tuple(batch), now)
                batch = []
    insert_events(db, tuple(batch), now)
    return issues + int(supported is None), False


def scan_file(db: sqlite3.Connection, path: Path, now: float) -> bool:
    try:
        before = signature(path)
        if previous_signature(db, path) == before:
            return False
        with path.open("rb") as stream:
            issues, pending = read_file(db, stream, now)
        # A concurrent append/rewrite must be revisited rather than checkpointed as read.
        after = signature(path)
        checkpoint(db, path, before if before == after else "changed-during-read", issues, pending)
    except OSError:
        checkpoint(db, path, "unreadable", 1, False)
    return True


def collect(db: sqlite3.Connection, roots: tuple[Path, ...], now: float) -> ScanResult:
    files, issues = discover(roots)
    with db:
        changed = sum(scan_file(db, path, now) for path in files)
        finish_scan(db, now, files, issues)
    return ScanResult(len(files), changed, issues)
