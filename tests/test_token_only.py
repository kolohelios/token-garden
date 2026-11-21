from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from garden.cli import arguments
from garden.display import garden_lines
from garden.runtime import CollectorCache
from garden.service import handler
from garden.ui import interact
from tests.helpers import NOW, sample
from tests.test_service_cli import request


def post(url: str, host: str | None) -> int:
    headers = {"Host": host} if host is not None else {}
    try:
        with urlopen(Request(url, data=b"{}", headers=headers), timeout=2) as response:
            return int(response.status)
    except HTTPError as error:
        return error.code


@pytest.mark.parametrize(
    "args",
    (
        ("show", "--system"),
        ("show", "--processes"),
        ("show", "--workloads"),
        ("show", "--diagnosis"),
        ("mark-slow",),
        ("show", "--window", "7d"),
        ("show", "--window", "90d"),
        ("ui", "--prometheus-port", "9097"),
    ),
)
def test_removed_commands_are_no_longer_available(
    monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    monkeypatch.setattr(sys, "argv", ["garden", *args])
    with pytest.raises(SystemExit):
        arguments()


def test_only_token_endpoints_and_metrics_remain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("garden.service.time.time", lambda: NOW)
    cache = CollectorCache()
    cache.publish_tokens((sample(),))
    with (
        HTTPServer(("127.0.0.1", 0), handler(cache)) as server,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        base = f"http://127.0.0.1:{server.server_port}"
        for path in ("/system", "/processes", "/diagnosis"):
            future = pool.submit(request, base + path, None)
            server.handle_request()
            assert future.result(timeout=3)[0] == 404
        future = pool.submit(request, base + "/metrics", None)
        server.handle_request()
        body = future.result(timeout=3)[2]
        names = {
            line.split("{", 1)[0].split(" ", 1)[0]
            for line in body.splitlines()
            if not line.startswith("#")
        }
        assert names == {
            "pi_token_window_tokens",
            "pi_garden_last_scan_timestamp_seconds",
            "pi_garden_coverage_issues",
            "pi_garden_session_files",
        }
        for route, host, expected in (
            ("/activity", None, 404),
            ("/slow", None, 404),
            ("/slow", "evil.example", 403),
        ):
            posting = pool.submit(post, base + route, host)
            server.handle_request()
            assert posting.result(timeout=3) == expected
        assert cache.read(NOW).token(NOW, 3600) == sample()


def test_old_resource_keys_do_not_switch_the_token_popup(monkeypatch: pytest.MonkeyPatch) -> None:
    import curses

    screen = Mock(spec=curses.window)
    screen.getmaxyx.return_value = (25, 100)
    screen.getch.side_effect = [ord(key) for key in "gcepv![]q"]
    monkeypatch.setattr("garden.ui.init_colors", lambda: False)
    monkeypatch.setattr(curses, "curs_set", Mock())
    monkeypatch.setattr(curses, "set_escdelay", Mock())
    client = Mock()
    client.fetch.return_value = sample()
    interact(screen, client)
    text = "\n".join(call.args[2] for call in screen.addstr.call_args_list)
    assert "token garden" in text and "46 tokens" in text
    assert "My Mac" not in text and "overload" not in text
    legend = "\n".join(line.text for line in garden_lines(sample(), 100))
    assert "h last hour" in legend and "d last day" in legend
    assert "why slow" not in legend and "procs" not in legend
