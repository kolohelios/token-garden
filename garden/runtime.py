from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, Lock

from garden.collector import collect
from garden.core import Snapshot, Window, health
from garden.ledger import connect, snapshot

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cached:
    tokens: tuple[Snapshot, ...] = ()
    token_failed: bool = False

    def token(self, now: float, window: Window) -> Snapshot | None:
        data = next((row for row in self.tokens if row.window_seconds == window), None)
        if data is None:
            return None
        status = "stale" if self.token_failed else health(now, data.updated_at, data.warnings)
        return data.model_copy(update={"now": now, "status": status})


class CollectorCache:
    def __init__(self) -> None:
        self._lock = Lock()
        self._data = Cached()

    def read(self, now: float) -> Cached:
        with self._lock:
            return self._data

    def publish_tokens(self, tokens: tuple[Snapshot, ...]) -> None:
        with self._lock:
            self._data = replace(self._data, tokens=tokens, token_failed=False)

    def fail_tokens(self) -> None:
        with self._lock:
            self._data = replace(self._data, token_failed=True)


def sample_tokens(
    db_path: Path, roots: tuple[Path, ...], cache: CollectorCache, stop: Event
) -> None:
    while not stop.is_set():
        try:
            # The worker owns its connection; HTTP never queries or shares SQLite.
            with closing(connect(db_path)) as db:
                now = time.time()
                result = collect(db, roots, now)
                cache.publish_tokens((snapshot(db, now, 3600), snapshot(db, now, 86400)))
            LOGGER.info("Scan: %s files, %s changed", result.files, result.changed)
        except (OSError, sqlite3.Error):
            cache.fail_tokens()
            LOGGER.warning("Collection failed; prior token data is stale")
        stop.wait(5)
