from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from garden.core import ModelUsage, Snapshot, Usage

SPARKS = "▁▂▃▄▅▆▇█"


@dataclass(frozen=True)
class Line:
    text: str
    color: int = 0
    bold: bool = False


def width(text: str) -> int:
    return sum(
        0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        for c in text
    )


def clip(text: str, columns: int) -> str:
    if columns <= 0:
        return ""
    if width(text) <= columns:
        return text
    result = ""
    for char in text:
        if width(result + char) > columns - 1:
            break
        result += char
    return result + "…"


def pad(text: str, columns: int) -> str:
    value = clip(text, columns)
    return value + " " * max(0, columns - width(value))


def compact(number: int) -> str:
    for scale, suffix in ((10**9, "B"), (10**6, "M"), (10**3, "k")):
        if number >= scale:
            return f"{number / scale:.1f}{suffix}"
    return str(number)


def sparkline(buckets: tuple[int, ...]) -> str:
    peak = max(buckets, default=0)
    return "".join(
        "·" if value == 0 else SPARKS[min(7, math.ceil(value / peak * 8) - 1)] for value in buckets
    )


def short_name(row: ModelUsage) -> str:
    return row.model.rsplit("/", 1)[-1]


def label(row: ModelUsage, rows: tuple[ModelUsage, ...]) -> str:
    if sum(short_name(other) == short_name(row) for other in rows) > 1:
        return f"{row.provider}/{row.model}"
    return short_name(row)


def status_line(data: Snapshot | None, columns: int = 110) -> str:
    if data is None:
        return "🌱 offline"
    prefix = "" if data.status == "ready" else f"{data.status} · "
    active = tuple(row for row in data.rows if row.usage.total)
    entries = tuple(f"{label(row, active)} {compact(row.usage.total)}/h" for row in active[:2])
    summary = " · ".join(entries) if entries else "no usage"
    extra = f" · +{len(active) - 2}" if len(active) > 2 else ""
    return clip(f"🌱 {prefix}{summary}{extra}", columns)


def breakdown(usage: Usage) -> str:
    return (
        f"↑ {compact(usage.input)} fresh   ↓ {compact(usage.output)} out   "
        f"R {compact(usage.cache_read)} cached   W {compact(usage.cache_write)} write"
    )


def model_lines(row: ModelUsage, color: int, columns: int) -> tuple[Line, ...]:
    name_width = max(1, columns - 17)
    title = pad(row.model, name_width) + f" {compact(row.usage.total):>9} tok"
    peak = max(row.buckets, default=0)
    return (
        Line("✿ " + title, color, True),
        Line(f"  {row.provider}", 1),
        Line(f"  {sparkline(row.buckets)}  peak {compact(peak)}/bucket", color),
        Line("  " + breakdown(row.usage), 1),
        Line(""),
    )


def garden_lines(data: Snapshot | None, columns: int) -> tuple[Line, ...]:
    if data is None:
        return (
            Line("✿ token garden", 2, True),
            Line(""),
            Line("Collector offline. Start: ./garden.sh serve", 4),
            Line(""),
            Line("h last hour · d last day · ↑↓ scroll · q/Esc close", 1),
        )
    window = (
        "last 60 minutes · 5-minute buckets"
        if data.window_seconds == 3600
        else "last 24 hours · hourly buckets"
    )
    start = datetime.fromtimestamp(data.now - data.window_seconds).strftime("%H:%M")
    end = datetime.fromtimestamp(data.now).strftime("%H:%M")
    cache_pct = data.total.cache_read / data.total.total * 100 if data.total.total else 0
    heading = (
        Line("✿ token garden", 2, True),
        Line(window, 1),
        Line(
            f"{compact(data.total.total)} tokens  ·  {cache_pct:.0f}% cache reads"
            f"  ·  {start} → {end}",
            3,
        ),
        Line(breakdown(data.total), 1),
        Line("─" * max(0, columns), 1),
    )
    flowers = tuple(
        line
        for index, row in enumerate(data.rows)
        for line in model_lines(row, 2 + index % 4, columns)
    ) or (Line("Your garden is resting. No usage in this window.", 3), Line(""))
    age = max(0, int(data.now - data.updated_at)) if data.updated_at else 0
    state = f"{data.status} · refreshed {age}s ago · {data.session_files} session files"
    warnings = (
        (Line(f"Partial coverage: {data.warnings} source issues", 4),) if data.warnings else ()
    )
    return (
        heading
        + flowers
        + (
            Line("─" * max(0, columns), 1),
            Line(state, 1 if data.status == "ready" else 4),
            *warnings,
            Line("Saved Pi usage only · ephemeral runs excluded · not billing", 1),
            Line("h last hour · d last day · ↑↓ scroll · q/Esc close", 1),
        )
    )


def render_plain(data: Snapshot | None, columns: int) -> str:
    inside = max(1, columns - 4)
    lines = garden_lines(data, inside)
    return "\n".join(
        ("╭" + "─" * (inside + 2) + "╮",)
        + tuple("│ " + pad(line.text, inside) + " │" for line in lines)
        + ("╰" + "─" * (inside + 2) + "╯",)
    )
