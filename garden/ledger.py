from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from garden.core import (
    RETENTION_SECONDS,
    Event,
    ModelUsage,
    Snapshot,
    Usage,
    Window,
    bucket_index,
    health,
    sum_usage,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    identity TEXT PRIMARY KEY, at REAL NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL,
    input INTEGER NOT NULL, output INTEGER NOT NULL,
    cache_read INTEGER NOT NULL, cache_write INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, signature TEXT NOT NULL,
    issues INTEGER NOT NULL, pending INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value REAL NOT NULL);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("ab"):
        pass
    path.chmod(0o600)
    db = sqlite3.connect(path, autocommit=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.commit()
    return db


def insert_events(db: sqlite3.Connection, events: tuple[Event, ...], now: float) -> None:
    db.executemany(
        "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                e.identity,
                e.at,
                e.provider,
                e.model,
                e.usage.input,
                e.usage.output,
                e.usage.cache_read,
                e.usage.cache_write,
            )
            for e in events
            if e.at > now - RETENTION_SECONDS
        ),
    )


def checkpoint(
    db: sqlite3.Connection, path: Path, signature: str, issues: int, pending: bool
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)",
        (str(path), signature, issues, int(pending)),
    )


def previous_signature(db: sqlite3.Connection, path: Path) -> str | None:
    row = db.execute("SELECT signature FROM files WHERE path = ?", (str(path),)).fetchone()
    return str(row["signature"]) if row is not None else None


def finish_scan(
    db: sqlite3.Connection, now: float, files: tuple[Path, ...], root_issues: int
) -> None:
    db.execute("DELETE FROM events WHERE at <= ?", (now - RETENTION_SECONDS,))
    db.execute("CREATE TEMP TABLE IF NOT EXISTS seen_paths (path TEXT PRIMARY KEY)")
    db.execute("DELETE FROM seen_paths")
    db.executemany("INSERT OR IGNORE INTO seen_paths VALUES (?)", ((str(p),) for p in files))
    db.execute("DELETE FROM files WHERE path NOT IN (SELECT path FROM seen_paths)")
    db.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?, ?)",
        (("updated_at", now), ("root_issues", root_issues)),
    )


def model_rows(db: sqlite3.Connection, now: float, window: Window) -> tuple[ModelUsage, ...]:
    groups: dict[tuple[str, str], list[tuple[float, Usage]]] = defaultdict(list)
    records = db.execute(
        "SELECT at, provider, model, input, output, cache_read, cache_write "
        "FROM events WHERE at > ? AND at <= ?",
        (now - window, now),
    )
    for record in records:
        usage = Usage.model_validate(dict(record))
        groups[(record["provider"], record["model"])].append((record["at"], usage))
    rows = tuple(
        make_row(provider, model, values, now, window)
        for (provider, model), values in groups.items()
    )
    return tuple(sorted(rows, key=lambda row: (-row.usage.total, row.provider, row.model)))


def make_row(
    provider: str, model: str, values: list[tuple[float, Usage]], now: float, window: Window
) -> ModelUsage:
    count = 12 if window == 3600 else 24
    buckets = tuple(
        sum(usage.total for at, usage in values if bucket_index(at, now, window) == index)
        for index in range(count)
    )
    return ModelUsage(
        provider=provider,
        model=model,
        usage=sum_usage(tuple(usage for _, usage in values)),
        buckets=buckets,
    )


def snapshot(db: sqlite3.Connection, now: float, window: Window) -> Snapshot:
    meta = {str(row["key"]): float(row["value"]) for row in db.execute("SELECT * FROM meta")}
    diagnostics = db.execute(
        "SELECT COUNT(*) AS count, COALESCE(SUM(issues), 0) AS issues FROM files"
    ).fetchone()
    future = db.execute("SELECT COUNT(*) FROM events WHERE at > ?", (now,)).fetchone()[0]
    warnings = int(diagnostics["issues"] + meta.get("root_issues", 0) + future)
    updated_at = meta.get("updated_at", 0)
    return Snapshot(
        now=now,
        updated_at=updated_at,
        window_seconds=window,
        status=health(now, updated_at, warnings),
        warnings=warnings,
        session_files=int(diagnostics["count"]),
        rows=model_rows(db, now, window),
    )


def read_snapshot(path: Path, now: float, window: Window) -> Snapshot:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return snapshot(db, now, window)
