from __future__ import annotations

import curses
import time
from contextlib import suppress
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import ValidationError

from garden.core import Snapshot, Window
from garden.display import Line, clip, garden_lines


class Client:
    def __init__(self, port: int) -> None:
        self.port = port
        self.cache: dict[Window, Snapshot] = {}

    def fetch(self, window: Window) -> Snapshot | None:
        try:
            with urlopen(
                f"http://127.0.0.1:{self.port}/usage?window={window}", timeout=0.7
            ) as response:
                data = Snapshot.model_validate_json(response.read(1024 * 1024))
            self.cache[window] = data
            return data
        except (OSError, URLError, ValidationError):
            cached = self.cache.get(window)
            if cached is None:
                return None
            return cached.model_copy(update={"status": "stale", "now": time.time()})


def init_colors() -> bool:
    if not curses.has_colors():
        return False
    curses.use_default_colors()
    palette = (245, 109, 150, 180, 139) if curses.COLORS >= 256 else (7, 6, 2, 3, 5)
    for index, color in enumerate(palette, start=1):
        curses.init_pair(index, color, -1)
    return True


def border(screen: curses.window, rows: int, columns: int) -> None:
    attr = curses.A_DIM
    screen.addstr(0, 0, "╭" + "─" * (columns - 2) + "╮", attr)
    for y in range(1, rows - 1):
        screen.addstr(y, 0, "│", attr)
        screen.addstr(y, columns - 1, "│", attr)
    screen.addstr(rows - 1, 0, "╰" + "─" * (columns - 2), attr)
    # insstr can fill the bottom-right cell without triggering curses' scroll error.
    screen.insstr(rows - 1, columns - 1, "╯", attr)


def paint(screen: curses.window, data: Snapshot | None, offset: int, colored: bool) -> int:
    rows, columns = screen.getmaxyx()
    screen.erase()
    inside = max(1, columns - 4)
    lines = garden_lines(data, inside)
    if data is not None and rows >= 12:
        footer_size = 5 if data.warnings else 4
        header, body, footer = lines[:5], lines[5:-footer_size], lines[-footer_size:]
        available = max(0, rows - 2 - len(header) - len(footer))
        offset = min(offset, max(0, len(body) - available))
        visible = header + body[offset : offset + available] + footer
    else:
        offset = min(offset, max(0, len(lines) - max(1, rows - 2)))
        visible = lines[offset : offset + max(0, rows - 2)]
    paint_visible(screen, visible, colored)
    return offset


def paint_visible(screen: curses.window, visible: tuple[Line, ...], colored: bool) -> None:
    rows, columns = screen.getmaxyx()
    inside = max(1, columns - 4)
    if rows > 2 and columns > 5:
        border(screen, rows, columns)
    for y, line in enumerate(visible, start=1):
        attr = curses.color_pair(line.color) if colored and line.color else 0
        attr |= curses.A_BOLD if line.bold else 0
        screen.addstr(y, min(2, max(0, columns - 2)), clip(line.text, inside), attr)
    screen.refresh()


def interact(screen: curses.window, client: Client) -> None:
    colored = init_colors()
    curses.set_escdelay(100)
    with suppress(curses.error):
        curses.curs_set(0)
    screen.keypad(True)
    screen.timeout(250)
    window: Window = 3600
    offset = 0
    next_fetch = 0.0
    data: Snapshot | None = None
    while True:
        if time.monotonic() >= next_fetch:
            data = client.fetch(window)
            next_fetch = time.monotonic() + 5
        with suppress(curses.error):
            offset = paint(screen, data, offset, colored)
        key = screen.getch()
        match key:
            case 27 | 113:
                return
            case 104 | 100:
                window = 3600 if key == 104 else 86400
                offset, next_fetch = 0, 0
            case curses.KEY_DOWN | 106:
                offset += 1
            case curses.KEY_UP | 107:
                offset = max(0, offset - 1)
            case curses.KEY_NPAGE:
                offset += max(1, screen.getmaxyx()[0] - 3)
            case curses.KEY_PPAGE:
                offset = max(0, offset - max(1, screen.getmaxyx()[0] - 3))


def run_ui(port: int) -> None:
    curses.wrapper(interact, Client(port))
