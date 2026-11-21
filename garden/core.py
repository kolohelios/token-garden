from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError, field_validator

Window = Literal[3600, 86400]
Health = Literal["indexing", "ready", "partial", "stale"]
TokenCount = Annotated[int, Field(strict=True, ge=0, le=10**15)]
STALE_SECONDS = 30
RETENTION_SECONDS = 30 * 86400


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")


class Usage(FrozenModel):
    input: TokenCount
    output: TokenCount
    cache_read: TokenCount = Field(alias="cacheRead")
    cache_write: TokenCount = Field(alias="cacheWrite")

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def plus(self, other: Usage) -> Usage:
        return Usage(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
        )


ZERO = Usage(input=0, output=0, cache_read=0, cache_write=0)


class SourceMessage(FrozenModel):
    role: str
    provider: str | None = None
    model: str | None = None
    usage: Usage | None = None


class SourceEntry(FrozenModel):
    type: str
    id: str = ""
    timestamp: str = ""
    version: int | None = None
    message: SourceMessage | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class Event:
    identity: str
    at: float
    provider: str
    model: str
    usage: Usage


@dataclass(frozen=True)
class Parsed:
    entry: SourceEntry | None = None
    event: Event | None = None
    issue: str | None = None


def safe_text(value: str) -> bool:
    return bool(value) and len(value) <= 200 and all(c.isprintable() for c in value)


def event_from_entry(entry: SourceEntry) -> Event | None:
    match entry.type:
        case "message" if entry.message is not None:
            message = entry.message
            if message.role not in ("assistant", "toolResult"):
                return None
            if message.usage is None:
                if message.role == "assistant":
                    raise ValueError("missing_usage")
                return None
            provider, model, usage = message.provider, message.model, message.usage
        case "compaction" | "branch_summary" if entry.usage is not None:
            provider, model, usage = None, None, entry.usage
        case _:
            return None
    at = datetime.fromisoformat(entry.timestamp)
    if at.tzinfo is None or not safe_text(entry.id):
        raise ValueError("invalid_identity")
    provider, model = provider or "unattributed", model or "unattributed"
    if not safe_text(provider) or not safe_text(model):
        raise ValueError("invalid_model")
    identity = hashlib.sha256(
        repr((entry.id, at.timestamp(), entry.type, provider, model, usage.model_dump())).encode()
    ).hexdigest()
    return Event(identity, at.timestamp(), provider, model, usage)


def parse_line(line: bytes) -> Parsed:
    try:
        entry = SourceEntry.model_validate_json(line)
        return Parsed(entry=entry, event=event_from_entry(entry))
    except (ValidationError, ValueError, OverflowError):
        # Validation errors can contain conversation content; never log their text.
        return Parsed(issue="invalid_entry")


class ModelUsage(FrozenModel):
    provider: str
    model: str
    usage: Usage
    buckets: tuple[TokenCount, ...]

    @field_validator("provider", "model")
    @classmethod
    def printable_label(cls, value: str) -> str:
        if not safe_text(value):
            raise ValueError("invalid display label")
        return value


class Snapshot(FrozenModel):
    now: FiniteFloat
    updated_at: FiniteFloat
    window_seconds: Window
    status: Health
    warnings: int = Field(ge=0)
    session_files: int = Field(ge=0)
    rows: tuple[ModelUsage, ...]

    @property
    def total(self) -> Usage:
        return sum_usage(tuple(row.usage for row in self.rows))


def sum_usage(values: tuple[Usage, ...]) -> Usage:
    return Usage(
        input=sum(value.input for value in values),
        output=sum(value.output for value in values),
        cache_read=sum(value.cache_read for value in values),
        cache_write=sum(value.cache_write for value in values),
    )


def bucket_index(at: float, now: float, window: Window) -> int:
    count = 12 if window == 3600 else 24
    return max(0, min(count - 1, math.ceil((at - (now - window)) / (window / count)) - 1))


def health(now: float, updated_at: float, warnings: int) -> Health:
    if updated_at == 0:
        return "indexing"
    if now - updated_at > STALE_SECONDS:
        return "stale"
    return "partial" if warnings else "ready"
