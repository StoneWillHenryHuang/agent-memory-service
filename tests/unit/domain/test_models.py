"""Tests for immutable domain values and serialization boundaries."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    FrozenJsonObject,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    MemoryScope,
)
from tests.unit.domain._factories import NOW, make_record, make_scope


def test_scope_normalizes_unicode_and_has_a_safe_repr() -> None:
    scope = MemoryScope(subject_id="cafe\N{COMBINING ACUTE ACCENT}", namespace="personal")

    assert scope.subject_id == "caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    assert scope.tenant_id is None
    assert repr(scope) == (
        "MemoryScope(tenant_id=<none>, subject_id=<redacted>, namespace=<redacted>)"
    )
    assert "café" not in repr(scope)


@pytest.mark.parametrize(
    "subject_id",
    ["", " subject", "subject id", "*", "a\x00b", "x" * 256],
)
def test_scope_rejects_invalid_identifiers(subject_id: str) -> None:
    with pytest.raises(DomainValidationError):
        MemoryScope(subject_id=subject_id)


def test_provenance_normalizes_time_and_identifiers() -> None:
    provenance = MemoryProvenance(
        source="api",
        session_id="session-1",
        event_timestamp=datetime(2026, 7, 30, 16, tzinfo=timezone(timedelta(hours=8))),
        input_digest="sha256:abc",
        model_id="model-1",
        prompt_version="v1",
        schema_version="v1",
    )

    assert provenance.event_timestamp == NOW
    assert provenance.source == "api"


def test_json_metadata_is_deeply_frozen_and_hashable() -> None:
    raw: dict[str, object] = {"tags": ["one", {"nested": True}], "count": 2}
    frozen = FrozenJsonObject(raw)
    raw["count"] = 3
    cast(list[object], raw["tags"]).append("later")

    assert frozen["count"] == 2
    assert frozen["tags"] == ("one", FrozenJsonObject({"nested": True}))
    assert list(frozen) == ["count", "tags"]
    assert len(frozen) == 2
    assert "keys=2" in repr(frozen)
    assert hash(FrozenJsonObject({"b": 2, "a": 1})) == hash(FrozenJsonObject({"a": 1, "b": 2}))


@pytest.mark.parametrize(
    "value",
    [float("inf"), float("nan"), {"bad": object()}],
)
def test_json_metadata_rejects_non_json_values(value: object) -> None:
    with pytest.raises(DomainValidationError):
        FrozenJsonObject({"value": value})


def test_json_metadata_rejects_bad_or_ambiguous_keys() -> None:
    with pytest.raises(DomainValidationError, match="strings"):
        FrozenJsonObject(cast("dict[str, object]", {1: "bad"}))
    with pytest.raises(DomainValidationError, match="empty"):
        FrozenJsonObject({"": "bad"})
    with pytest.raises(DomainValidationError, match="control"):
        FrozenJsonObject({"bad\nkey": "bad"})
    with pytest.raises(DomainValidationError, match="unique"):
        FrozenJsonObject(
            {
                "cafe\N{COMBINING ACUTE ACCENT}": 1,
                "caf\N{LATIN SMALL LETTER E WITH ACUTE}": 2,
            }
        )


def test_conversation_message_is_immutable_content_safe_and_normalized() -> None:
    message = ConversationMessage(
        role="user",
        content="private content",
        timestamp=datetime(2026, 7, 30, 16, tzinfo=timezone(timedelta(hours=8))),
        metadata={"turn": 1},
    )

    assert message.timestamp == NOW
    assert message.metadata == FrozenJsonObject({"turn": 1})
    assert "private content" not in repr(message)
    with pytest.raises(FrozenInstanceError):
        message.role = "assistant"  # type: ignore[misc]


@pytest.mark.parametrize("content", ["", "   ", "bad\x00content"])
def test_conversation_message_rejects_invalid_content(content: str) -> None:
    with pytest.raises(DomainValidationError):
        ConversationMessage(role="user", content=content)


def test_embedding_copies_values_and_exposes_dimension() -> None:
    values = [0, 1.5]
    embedding = Embedding(values=values, model_id="embed-v1", task=EmbeddingTask.DOCUMENT)
    values.append(2.0)

    assert embedding.values == (0.0, 1.5)
    assert embedding.dimension == 2
    assert "values" not in repr(embedding)


@pytest.mark.parametrize(
    "values",
    [[], [True], [float("inf")], cast("list[float]", ["bad"])],
)
def test_embedding_rejects_invalid_values(values: list[float]) -> None:
    with pytest.raises(DomainValidationError):
        Embedding(values=values, model_id="embed-v1", task=EmbeddingTask.DOCUMENT)


def test_memory_record_normalizes_time_and_is_hashable() -> None:
    local_time = datetime(2026, 7, 30, 16, tzinfo=timezone(timedelta(hours=8)))
    record = MemoryRecord(
        id="memory-1",
        scope=make_scope(),
        kind=MemoryKind.FACT,
        content="private fact",
        created_at=local_time,
        updated_at=local_time,
        metadata={"confidence": 0.9},
        version=1,
    )

    assert record.created_at == NOW
    assert record.updated_at == NOW
    assert record.metadata == FrozenJsonObject({"confidence": 0.9})
    assert hash(record) == hash(record)
    assert "private fact" not in repr(record)


def test_same_memory_id_in_different_scopes_is_not_equal() -> None:
    first = make_record(scope=make_scope(subject_id="first"))
    second = make_record(scope=make_scope(subject_id="second"))

    assert first != second
    assert len({first, second}) == 2


def test_memory_record_rejects_invalid_time_and_components() -> None:
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        MemoryRecord(
            id="memory-1",
            scope=make_scope(),
            kind=MemoryKind.FACT,
            content="fact",
            created_at=datetime(2026, 7, 30),
            updated_at=NOW,
        )
    with pytest.raises(DomainValidationError, match="earlier"):
        MemoryRecord(
            id="memory-1",
            scope=make_scope(),
            kind=MemoryKind.FACT,
            content="fact",
            created_at=NOW,
            updated_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(DomainValidationError, match="scope"):
        MemoryRecord(
            id="memory-1",
            scope=cast("MemoryScope", object()),
            kind=MemoryKind.FACT,
            content="fact",
            created_at=NOW,
            updated_at=NOW,
        )
