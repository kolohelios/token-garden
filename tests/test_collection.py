from __future__ import annotations

import io
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest

from garden.collector import collect, discover, read_file, scan_file
from garden.core import RETENTION_SECONDS
from garden.ledger import connect, read_snapshot, snapshot
from tests.helpers import NOW, entry, session


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "state" / "usage.sqlite3")
    yield connection
    connection.close()


def test_scan_restart_fork_model_switch_and_source_privacy(tmp_path: Path) -> None:
    roots = tmp_path / "sessions"
    roots.mkdir()
    original = roots / "original.jsonl"
    original.write_bytes(
        session(entry(at=NOW - 2), entry(at=NOW - 1, identity="new", model="sonnet"))
    )
    (roots / "fork.jsonl").write_bytes(session(entry(at=NOW - 2)))
    before = original.read_bytes()
    path = tmp_path / "state" / "ledger.sqlite3"
    db = connect(path)
    first = collect(db, (roots,), NOW)
    assert first.changed == 2
    assert collect(db, (roots,), NOW).changed == 0
    assert snapshot(db, NOW, 3600).total.total == 92
    assert {r.model for r in snapshot(db, NOW, 3600).rows} == {"opus", "sonnet"}
    db.close()
    db = connect(path)
    collect(db, (roots,), NOW)
    assert read_snapshot(path, NOW, 3600).total.total == 92
    db.close()
    assert original.read_bytes() == before
    assert b"PRIVATE CONVERSATION" not in path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600


def test_window_boundaries_and_future_events(db: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "data.jsonl").write_bytes(
        session(*(entry(at=NOW + offset, identity=str(offset)) for offset in (-3600, -3599, 0, 1)))
    )
    collect(db, (tmp_path,), NOW)
    current = snapshot(db, NOW, 3600)
    assert current.total.total == 92
    assert current.rows[0].buckets[0] == 46
    assert current.rows[0].buckets[-1] == 46
    assert current.status == "partial" and current.warnings == 1
    assert snapshot(db, NOW + 1, 3600).total.total == 92
    assert snapshot(db, NOW, 86400).total.total == 138


def test_partial_line_later_completed_and_bad_line_does_not_hide_valid_event(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    file = tmp_path / "data.jsonl"
    full = session(entry())
    file.write_bytes(full[:-5])
    collect(db, (tmp_path,), NOW)
    assert snapshot(db, NOW, 3600).total.total == 0
    file.write_bytes(
        full + b"not-json\n" + session(entry(identity="later"))[session().index(b"\n") + 1 :]
    )
    collect(db, (tmp_path,), NOW)
    assert snapshot(db, NOW, 3600).total.total == 92
    assert snapshot(db, NOW, 3600).warnings == 1


def test_rename_rewrite_truncate_delete_and_retention(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    file = tmp_path / "data.jsonl"
    file.write_bytes(session(entry(), entry(at=NOW - RETENTION_SECONDS - 1, identity="old")))
    collect(db, (tmp_path,), NOW)
    assert snapshot(db, NOW, 3600).total.total == 46
    renamed = tmp_path / "renamed.jsonl"
    file.rename(renamed)
    collect(db, (tmp_path,), NOW)
    assert snapshot(db, NOW, 3600).total.total == 46
    renamed.write_bytes(session(entry(identity="new", model="sonnet")))
    collect(db, (tmp_path,), NOW)
    assert snapshot(db, NOW, 3600).total.total == 92
    renamed.write_bytes(b"")
    collect(db, (tmp_path,), NOW)
    assert snapshot(db, NOW, 3600).status == "partial"
    renamed.unlink()
    collect(db, (tmp_path,), NOW)
    assert snapshot(db, NOW, 3600).total.total == 92
    assert snapshot(db, NOW, 3600).status == "ready"
    collect(db, (tmp_path,), NOW + RETENTION_SECONDS)
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


@pytest.mark.parametrize(
    "payload", [b"", b"not-json\n", session(entry(), version=99), session(entry(), version=1)]
)
def test_unsupported_or_bad_header_diagnosed(
    db: sqlite3.Connection, tmp_path: Path, payload: bytes
) -> None:
    (tmp_path / "data.jsonl").write_bytes(payload)
    collect(db, (tmp_path,), NOW)
    data = snapshot(db, NOW, 3600)
    assert data.status == "partial"
    assert data.total.total == 0


def test_missing_and_unreadable_roots(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collect(db, (tmp_path / "missing",), NOW)
    assert snapshot(db, NOW, 3600).status == "partial"
    walker = Mock(side_effect=lambda root, onerror, followlinks: (onerror(OSError()), ())[1])
    monkeypatch.setattr("garden.collector.os.walk", walker)
    assert discover((tmp_path,))[1] == 1


def test_unreadable_file_remains_retryable(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "data.jsonl"
    file.write_bytes(session(entry()))
    monkeypatch.setattr("garden.collector.signature", Mock(side_effect=PermissionError()))
    assert scan_file(db, file, NOW)
    assert db.execute("SELECT issues FROM files").fetchone()[0] == 1


def test_batch_and_oversized_lines(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    contents = session(*(entry(identity=str(i)) for i in range(257)))
    assert read_file(db, io.BytesIO(contents), NOW) == (0, False)
    assert snapshot(db, NOW, 3600).total.total == 46 * 257
    monkeypatch.setattr("garden.collector.MAX_LINE_BYTES", 80)
    issues, pending = read_file(db, io.BytesIO(session() + b"x" * 180 + b"\n"), NOW)
    assert issues == 1 and not pending


def test_changed_during_read_not_checkpointed_as_complete(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "data.jsonl"
    file.write_bytes(session(entry()))
    monkeypatch.setattr("garden.collector.signature", Mock(side_effect=["before", "after"]))
    assert scan_file(db, file, NOW)
    assert db.execute("SELECT signature FROM files").fetchone()[0] == "changed-during-read"


def test_scan_transaction_rolls_back_events_and_checkpoints(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data.jsonl").write_bytes(session(entry()))
    monkeypatch.setattr(
        "garden.collector.finish_scan", Mock(side_effect=sqlite3.OperationalError())
    )
    with pytest.raises(sqlite3.OperationalError):
        collect(db, (tmp_path,), NOW)
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
