from __future__ import annotations

import curses
import io
from unittest.mock import Mock

import pytest

from garden.core import ZERO, ModelUsage
from garden.display import (
    clip,
    compact,
    garden_lines,
    label,
    render_plain,
    sparkline,
    status_line,
    width,
)
from garden.ui import Client, init_colors, interact, paint, run_ui
from tests.helpers import sample


@pytest.mark.parametrize(
    "number,expected",
    [(0, "0"), (999, "999"), (1000, "1.0k"), (1500000, "1.5M"), (2000000000, "2.0B")],
)
def test_compact(number: int, expected: str) -> None:
    assert compact(number) == expected


@pytest.mark.parametrize("text", ["garden", "🌱 hello", "e\u0301", "界\x20token"])
def test_clip_respects_cells(text: str) -> None:
    for columns in range(0, 12):
        assert width(clip(text, columns)) <= columns
    assert width("e\u0301") == 1
    assert width("🌱") == 2


def test_sparkline_scale_and_empty() -> None:
    assert sparkline((0, 1, 2, 8)) == "·▁▂█"
    assert sparkline((0, 0)) == "··"
    assert sparkline(()) == ""


def test_status_health_empty_ranking_and_model_name_collisions() -> None:
    assert status_line(None) == "🌱 offline"
    data = sample()
    other = data.rows[0].model_copy(update={"provider": "openrouter"})
    assert "anthropic/claude-opus" in label(data.rows[0], (data.rows[0], other))
    assert "ready" not in status_line(data)
    assert "partial" in status_line(sample("partial"))
    assert "no usage" in status_line(data.model_copy(update={"rows": ()}))
    others = tuple(data.rows[0].model_copy(update={"model": f"model-{i}"}) for i in range(3))
    assert "+1" in status_line(data.model_copy(update={"rows": others}))
    assert width(status_line(data, 15)) <= 15


def test_render_fits_small_widths_and_shows_separate_cache() -> None:
    for columns in (12, 24, 40, 76, 110):
        assert all(width(line) == columns for line in render_plain(sample(), columns).splitlines())
    text = render_plain(sample(), 110)
    assert "PRIVATE CONVERSATION" not in text
    assert "cached" in text and "fresh" in text and "46 tokens" in text
    assert "offline" in render_plain(None, 76)
    assert "Partial coverage" in render_plain(sample("partial"), 76)
    day = sample().model_copy(update={"window_seconds": 86400, "rows": ()})
    assert any("24 hours" in line.text for line in garden_lines(day, 76))
    assert any("resting" in line.text for line in garden_lines(day, 76))
    indexing = sample().model_copy(update={"updated_at": 0})
    assert "refreshed 0s" in render_plain(indexing, 76)


def test_labels_are_validated_at_http_boundary() -> None:
    with pytest.raises(ValueError):
        ModelUsage(provider="\x1b", model="ok", usage=ZERO, buckets=())


def test_client_caches_only_matching_window_and_marks_failures_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client(9467)
    monkeypatch.setattr(
        "garden.ui.urlopen", lambda *args, **kwargs: io.BytesIO(sample().model_dump_json().encode())
    )
    assert client.fetch(3600) == sample()
    monkeypatch.setattr("garden.ui.urlopen", Mock(side_effect=OSError()))
    cached = client.fetch(3600)
    assert cached is not None and cached.status == "stale" and cached.total.total == 46
    assert client.fetch(86400) is None


def test_client_bad_json_not_treated_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("garden.ui.urlopen", lambda *args, **kwargs: io.BytesIO(b"{}"))
    assert Client(9467).fetch(3600) is None


@pytest.mark.parametrize("colors", [8, 256])
def test_color_setup(colors: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curses, "has_colors", lambda: True)
    monkeypatch.setattr(curses, "COLORS", colors, raising=False)
    monkeypatch.setattr(curses, "use_default_colors", Mock())
    pairs = Mock()
    monkeypatch.setattr(curses, "init_pair", pairs)
    assert init_colors()
    assert pairs.call_count == 5
    monkeypatch.setattr(curses, "has_colors", lambda: False)
    assert not init_colors()


def test_paint_clamps_scroll_and_respects_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = Mock(spec=curses.window)
    screen.getmaxyx.return_value = (14, 44)
    monkeypatch.setattr(curses, "color_pair", lambda pair: pair)
    assert paint(screen, sample(), 999, True) >= 0
    assert screen.addstr.call_args_list[0].args[2].startswith("╭")
    for call in screen.addstr.call_args_list:
        y, x, text, _ = call.args
        assert y < 14 and x + width(text) <= 44
    screen.insstr.assert_called_once()
    screen.getmaxyx.return_value = (1, 1)
    assert paint(screen, None, 999, False) >= 0


def test_interaction_switches_windows_scrolls_and_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = Mock(spec=curses.window)
    screen.getmaxyx.return_value = (14, 76)
    screen.getch.side_effect = [
        curses.KEY_DOWN,
        curses.KEY_UP,
        curses.KEY_NPAGE,
        curses.KEY_PPAGE,
        ord("d"),
        ord("h"),
        ord("q"),
    ]
    monkeypatch.setattr("garden.ui.init_colors", lambda: False)
    monkeypatch.setattr(curses, "curs_set", Mock(side_effect=curses.error()))
    escape_delay = Mock()
    monkeypatch.setattr(curses, "set_escdelay", escape_delay)
    client = Mock(spec=Client)
    client.port = 9467
    client.fetch.return_value = sample()
    interact(screen, client)
    assert [call.args[0] for call in client.fetch.call_args_list] == [3600, 86400, 3600]
    assert screen.refresh.call_count == 7
    escape_delay.assert_called_once_with(100)


def test_ui_wrapper_restores_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = Mock()
    monkeypatch.setattr(curses, "wrapper", wrapper)
    run_ui(9467)
    assert wrapper.call_args.args[0] == interact
