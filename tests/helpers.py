from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, datetime
from typing import ParamSpec, Self, TypeVar

from garden.core import ModelUsage, Snapshot, Usage

P = ParamSpec("P")
T = TypeVar("T")


class ImmediatePool:
    def __init__(self, max_workers: int = 2) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def submit(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        future: Future[T] = Future()
        future.set_result(function(*args, **kwargs))
        return future


NOW = 1791216000.0


def entry(
    at: float = NOW,
    identity: str = "entry001",
    model: str = "opus",
    provider: str = "anthropic",
    role: str = "assistant",
    tokens: int = 10,
) -> dict[str, object]:
    return {
        "type": "message",
        "id": identity,
        "timestamp": datetime.fromtimestamp(at, UTC).isoformat(),
        "message": {
            "role": role,
            "provider": provider,
            "model": model,
            "content": [{"type": "text", "text": "PRIVATE CONVERSATION"}],
            "usage": {
                "input": tokens,
                "output": 2,
                "cacheRead": 30,
                "cacheWrite": 4,
                "totalTokens": 999999,
                "cost": {"total": 100},
            },
        },
    }


def line(value: dict[str, object]) -> bytes:
    return json.dumps(value).encode() + b"\n"


def session(*events: dict[str, object], version: int = 3) -> bytes:
    return line({"type": "session", "version": version}) + b"".join(line(e) for e in events)


def sample(status: str = "ready") -> Snapshot:
    return Snapshot.model_validate(
        {
            "now": NOW,
            "updated_at": NOW,
            "window_seconds": 3600,
            "status": status,
            "session_files": 2,
            "warnings": 1 if status == "partial" else 0,
            "rows": [
                ModelUsage(
                    provider="anthropic",
                    model="claude-opus",
                    usage=Usage(input=10, output=2, cache_read=30, cache_write=4),
                    buckets=(0,) * 11 + (46,),
                ).model_dump()
            ],
        }
    )
