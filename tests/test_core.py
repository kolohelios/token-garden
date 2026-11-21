from __future__ import annotations

import pytest
from pydantic import ValidationError

from garden.core import ZERO, ModelUsage, Usage, bucket_index, health, parse_line, sum_usage
from tests.helpers import NOW, entry, line, sample


def test_usage_categories_not_reported_total() -> None:
    result = parse_line(line(entry()))
    assert result.event is not None
    assert result.event.usage.total == 46
    assert ZERO.plus(result.event.usage) == result.event.usage
    assert sum_usage((result.event.usage, result.event.usage)).total == 92


@pytest.mark.parametrize("role", ["user", "custom", "bashExecution"])
def test_non_model_messages_do_not_count(role: str) -> None:
    assert parse_line(line(entry(role=role))).event is None


@pytest.mark.parametrize("value", [-1, True, 1.5, "10", None, 10**16])
def test_invalid_token_counts_are_diagnosed(value: object) -> None:
    raw = entry()
    message = raw["message"]
    assert isinstance(message, dict)
    usage = message["usage"]
    assert isinstance(usage, dict)
    usage["input"] = value
    assert parse_line(line(raw)).issue == "invalid_entry"


def test_missing_usage_differs_from_zero_usage() -> None:
    raw = entry()
    message = raw["message"]
    assert isinstance(message, dict)
    message.pop("usage")
    assert parse_line(line(raw)).issue is not None
    message["role"] = "toolResult"
    assert parse_line(line(raw)).issue is None
    raw = entry(tokens=0)
    assert parse_line(line(raw)).event is not None


@pytest.mark.parametrize("kind", ["compaction", "branch_summary"])
def test_summary_usage_unattributed_and_retained_tail_not_counted(kind: str) -> None:
    raw = entry()
    raw["type"] = kind
    raw["retainedTail"] = [entry(tokens=999)]
    raw["usage"] = {"input": 3, "output": 1, "cacheRead": 0, "cacheWrite": 0}
    parsed = parse_line(line(raw))
    assert parsed.event is not None
    assert parsed.event.provider == "unattributed"
    assert parsed.event.usage.total == 4
    raw.pop("usage")
    assert parse_line(line(raw)).event is None


def test_tool_usage_without_attribution_is_not_guessed() -> None:
    raw = entry(role="toolResult")
    message = raw["message"]
    assert isinstance(message, dict)
    message.pop("model")
    message.pop("provider")
    parsed = parse_line(line(raw))
    assert parsed.event is not None
    assert parsed.event.model == "unattributed"


@pytest.mark.parametrize(
    "field,value", [("id", ""), ("timestamp", "bad"), ("timestamp", "2026-01-01"), ("id", "\x1b")]
)
def test_invalid_event_identity(field: str, value: str) -> None:
    raw = entry()
    raw[field] = value
    assert parse_line(line(raw)).issue is not None


@pytest.mark.parametrize("model", ["\x1b[31mEVIL", "line\nbreak", "x" * 201])
def test_terminal_controls_never_reach_ui(model: str) -> None:
    assert parse_line(line(entry(model=model))).issue is not None
    with pytest.raises(ValidationError):
        ModelUsage(provider="ok", model=model, usage=ZERO, buckets=())


def test_malformed_json_and_irrelevant_custom_entries() -> None:
    assert parse_line(b"invalid json").issue is not None
    assert parse_line(line({"type": "custom", "data": {"tokens": 999}})).event is None


def test_dedup_identity_ignores_parent_and_content_but_not_usage_or_model() -> None:
    original = entry()
    forked = {**original, "parentId": "another-parent"}
    first = parse_line(line(original)).event
    second = parse_line(line(forked)).event
    assert first is not None and second is not None
    assert first.identity == second.identity
    changed = parse_line(line(entry(tokens=11))).event
    switched = parse_line(line(entry(model="sonnet"))).event
    assert changed is not None and changed.identity != first.identity
    assert switched is not None and switched.identity != first.identity


@pytest.mark.parametrize(
    "offset,index", [(-3599, 0), (-3300, 0), (-3299, 1), (-300, 10), (-299, 11), (0, 11)]
)
def test_bucket_boundaries(offset: int, index: int) -> None:
    assert bucket_index(NOW + offset, NOW, 3600) == index
    assert bucket_index(NOW - 86400 + 1, NOW, 86400) == 0
    assert bucket_index(NOW, NOW, 86400) == 23


@pytest.mark.parametrize(
    "updated,warnings,expected",
    [(0, 0, "indexing"), (NOW, 0, "ready"), (NOW, 2, "partial"), (NOW - 31, 0, "stale")],
)
def test_health_uses_scan_not_last_model_response(
    updated: float, warnings: int, expected: str
) -> None:
    assert health(NOW, updated, warnings) == expected


def test_snapshot_roundtrip_and_totals() -> None:
    data = sample()
    assert type(data).model_validate_json(data.model_dump_json()) == data
    assert data.total.total == 46
    with pytest.raises(ValidationError):
        Usage(input=-1, output=0, cache_read=0, cache_write=0)
