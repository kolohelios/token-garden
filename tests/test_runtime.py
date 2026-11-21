from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from garden.runtime import CollectorCache, sample_tokens
from garden.service import serve
from tests.helpers import NOW, entry, sample, session
from tests.test_service_cli import request


def populated_cache() -> CollectorCache:
    cache = CollectorCache()
    cache.publish_tokens((sample(), sample().model_copy(update={"window_seconds": 86400})))
    return cache


def test_cached_tokens_age_fail_and_recover() -> None:
    cache = populated_cache()
    assert cache.read(NOW).token(NOW, 3600) == sample()
    assert cache.read(NOW).token(NOW, 86400) is not None
    cache.fail_tokens()
    assert cache.read(NOW).token_failed
    failed = cache.read(NOW).token(NOW, 3600)
    assert failed is not None and failed.status == "stale"
    cache.publish_tokens((sample(),))
    assert not cache.read(NOW).token_failed
    assert cache.read(NOW).token(NOW, 86400) is None
    aged = cache.read(NOW + 31).token(NOW + 31, 3600)
    assert aged is not None and aged.status == "stale" and aged.now == NOW + 31
    assert cache.read(NOW).token(NOW, 3600) == sample()
    assert CollectorCache().read(NOW).token(NOW, 3600) is None


def test_token_worker_owns_ledger_and_updates_both_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "session.jsonl").write_bytes(session(entry()))
    monkeypatch.setattr("garden.runtime.time.time", lambda: NOW)
    stop = Event()
    monkeypatch.setattr(stop, "wait", lambda seconds: stop.set())
    cache = CollectorCache()
    sample_tokens(tmp_path / "state/db.sqlite3", (tmp_path,), cache, stop)
    data = cache.read(NOW)
    assert len(data.tokens) == 2
    assert all(token.total.total == 46 for token in data.tokens)


@pytest.mark.parametrize("error", [OSError(), sqlite3.OperationalError()])
def test_failed_token_worker_preserves_data_but_not_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    cache = populated_cache()
    stop = Event()
    monkeypatch.setattr(stop, "wait", lambda seconds: stop.set())
    monkeypatch.setattr("garden.runtime.connect", Mock(side_effect=error))
    sample_tokens(tmp_path / "state/db.sqlite3", (tmp_path,), cache, stop)
    data = cache.read(NOW)
    assert data.token_failed and data.tokens[0].total.total == 46


def test_http_stays_responsive_while_token_scan_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = populated_cache()
    stop, release, entered = Event(), Event(), Event()

    def blocked_tokens(*args: object) -> None:
        entered.set()
        release.wait(5)

    monkeypatch.setattr("garden.service.CollectorCache", lambda: cache)
    monkeypatch.setattr("garden.service.sample_tokens", blocked_tokens)
    monkeypatch.setattr("garden.service.time.time", lambda: NOW)
    ports: list[int] = []

    class BoundServer(HTTPServer):
        def __init__(
            self, address: tuple[str, int], request_handler: type[BaseHTTPRequestHandler]
        ) -> None:
            super().__init__(address, request_handler)
            ports.append(self.server_port)

    monkeypatch.setattr("garden.service.HTTPServer", BoundServer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(serve, tmp_path / "unused.sqlite3", (), 0, stop)
        try:
            assert entered.wait(2)
            for path in ("/usage", "/metrics"):
                code, _, body = request(f"http://127.0.0.1:{ports[0]}{path}", None)
                assert code == 200 and body
            assert not future.done()
        finally:
            stop.set()
            release.set()
            future.result(timeout=3)
