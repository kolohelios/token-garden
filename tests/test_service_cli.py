from __future__ import annotations

import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from garden import cli
from garden.collector import collect
from garden.core import Snapshot
from garden.ledger import connect, snapshot
from garden.runtime import CollectorCache
from garden.service import handler, metrics, run_service, serve
from tests.helpers import NOW, entry, sample, session


def request(url: str, host: str | None) -> tuple[int, str, str]:
    headers = {"Host": host} if host is not None else {}
    try:
        with urlopen(Request(url, headers=headers), timeout=3) as response:
            return response.status, response.headers["Content-Type"], response.read().decode()
    except HTTPError as error:
        return error.code, error.headers["Content-Type"], error.read().decode()


def test_http_metrics_query_windows_and_localhost_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = connect(tmp_path / "db.sqlite3")
    (tmp_path / "session.jsonl").write_bytes(session(entry()))
    collect(db, (tmp_path,), NOW)
    monkeypatch.setattr("garden.service.time.time", lambda: NOW)
    cache = CollectorCache()
    cache.publish_tokens((snapshot(db, NOW, 3600), snapshot(db, NOW, 86400)))
    with (
        HTTPServer(("127.0.0.1", 0), handler(cache)) as server,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        server.timeout = 2
        base = f"http://127.0.0.1:{server.server_port}"
        for path, host, expected in (
            ("/usage", None, 200),
            ("/usage?window=86400", None, 200),
            ("/usage?window=bad", None, 400),
            ("/metrics", None, 200),
            ("/unknown", None, 404),
            ("/usage", "evil.example", 403),
        ):
            future = pool.submit(request, base + path, host)
            server.handle_request()
            code, content_type, body = future.result(timeout=3)
            assert code == expected
            assert "PRIVATE CONVERSATION" not in body
            if path == "/usage" and code == 200:
                assert Snapshot.model_validate_json(body).total.total == 46
            if path == "/metrics":
                assert "version=0.0.4" in content_type
                assert 'kind="cache_read",window="3600s"} 30' in body
            if path.endswith("86400"):
                assert Snapshot.model_validate_json(body).window_seconds == 86400
    db.close()


def test_metrics_label_escaping_and_no_historical_counters() -> None:
    data = sample()
    row = data.rows[0].model_copy(update={"model": '模型\\"test'})
    output = metrics((data.model_copy(update={"rows": (row,)}),))
    assert '模型\\\\\\"test' in output
    assert "counter" not in output
    assert "PRIVATE" not in output
    assert "last_scan_timestamp_seconds" in output
    for name in (
        "pi_token_window_tokens",
        "pi_garden_last_scan_timestamp_seconds",
        "pi_garden_coverage_issues",
        "pi_garden_session_files",
    ):
        assert f"# HELP {name} " in output
    assert metrics(()) == (
        "# HELP pi_token_window_tokens Observed Pi tokens in a trailing wall-clock window.\n"
        "# TYPE pi_token_window_tokens gauge\n"
    )


@pytest.mark.parametrize("fails", [False, True])
def test_service_loop_closes_resources_after_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    stop = Event()
    server = Mock(spec=HTTPServer)
    server.server_port = 9467
    server.handle_request.side_effect = OSError() if fails else stop.set
    server.__enter__ = Mock(return_value=server)
    server.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("garden.service.HTTPServer", Mock(return_value=server))
    workers = (Mock(ident=1),)
    factory = Mock(side_effect=workers)
    monkeypatch.setattr("garden.service.Thread", factory)
    if fails:
        with pytest.raises(OSError):
            serve(tmp_path / "db.sqlite3", (tmp_path,), 9467, stop)
    else:
        serve(tmp_path / "db.sqlite3", (tmp_path,), 9467, stop)
    assert stop.is_set()
    assert factory.call_count == 1
    assert factory.call_args.kwargs["name"] == "garden-tokens"
    for worker in workers:
        worker.start.assert_called_once()
        worker.join.assert_called_once()
    server.__exit__.assert_called_once()


def test_graceful_signal_sets_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    signals = Mock()
    monkeypatch.setattr(signal, "signal", signals)
    service = Mock()
    monkeypatch.setattr("garden.service.serve", service)
    run_service(tmp_path / "db.sqlite3", (tmp_path,), 9467)
    callback = signals.call_args_list[0].args[1]
    stop = service.call_args.args[-1]
    assert not stop.is_set()
    callback(signal.SIGTERM, None)
    assert stop.is_set()


@pytest.mark.parametrize("args", [["status", "--port", "0"], ["show", "--width", "1"]])
def test_cli_rejects_invalid_bounds(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["garden", *args])
    with pytest.raises(SystemExit):
        cli.arguments()


def test_cli_scan_and_offline_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "session.jsonl").write_bytes(session(entry()))
    monkeypatch.setattr("garden.cli.time.time", lambda: NOW)
    monkeypatch.setattr("garden.cli.os.umask", Mock())
    base = ["garden", "scan", "--state", str(tmp_path / "state"), "--sessions", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", base)
    assert cli.main() == 0
    monkeypatch.setattr(
        sys, "argv", ["garden", "show", "--state", str(tmp_path / "state"), "--offline"]
    )
    assert cli.main() == 0
    assert "46 tokens" in capsys.readouterr().out
    monkeypatch.setattr(
        sys, "argv", ["garden", "status", "--state", str(tmp_path / "missing"), "--offline"]
    )
    assert cli.main() == 0
    assert "offline" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["serve", "ui", "show", "status"])
def test_cli_routes_command(
    monkeypatch: pytest.MonkeyPatch, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["garden", command, "--window", "24h"])
    monkeypatch.setattr("garden.cli.os.umask", Mock())
    service = Mock()
    ui = Mock()
    client = Mock()
    client.fetch.return_value = sample()
    monkeypatch.setattr("garden.cli.run_service", service)
    monkeypatch.setattr("garden.cli.run_ui", ui)
    monkeypatch.setattr("garden.cli.Client", Mock(return_value=client))
    assert cli.main() == 0
    if command == "serve":
        service.assert_called_once()
    elif command == "ui":
        ui.assert_called_once()
    else:
        assert "garden" in capsys.readouterr().out or command == "status"
        assert client.fetch.call_args.args[0] == (3600 if command == "status" else 86400)
