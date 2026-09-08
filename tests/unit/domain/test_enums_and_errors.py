"""Tests for extensible values and sanitized public failures."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

import portable_memory_engine.domain as domain
from portable_memory_engine.domain import (
    CapabilityError,
    ConflictError,
    CountPrecision,
    DeleteTarget,
    DomainValidationError,
    EmbeddingTask,
    IdempotencyConflictError,
    MemoryEngineError,
    MemoryEvent,
    MemoryKind,
    ProviderError,
    ProviderParseError,
    PutStatus,
    SortOrder,
    StaleEventError,
)


def test_memory_kind_has_stable_defaults_and_namespaced_extensions() -> None:
    custom = MemoryKind("acme:episodic")

    assert str(MemoryKind.SUMMARY) == "summary"
    assert MemoryKind.FACT.is_default is True
    assert custom.is_default is False
    assert sorted({MemoryKind.PROFILE, MemoryKind.FACT}) == [MemoryKind.FACT, MemoryKind.PROFILE]


@pytest.mark.parametrize("value", ["", "episodic", "ACME:fact", "acme:", "1acme:fact"])
def test_memory_kind_rejects_unstable_extensions(value: str) -> None:
    with pytest.raises(DomainValidationError):
        MemoryKind(value)


def test_string_enums_have_stable_wire_values() -> None:
    assert [event.value for event in MemoryEvent] == ["add", "update", "delete", "noop"]
    assert EmbeddingTask.QUERY.value == "query"
    assert SortOrder.NEWEST.value == "newest"
    assert CountPrecision.APPROXIMATE.value == "approximate"
    assert PutStatus.CREATED.value == "created"
    assert DeleteTarget.SCOPE.value == "scope"


def test_error_hierarchy_and_messages_are_sanitized() -> None:
    capability = CapabilityError(operation="semantic_search", capability="vector_search")
    conflict = ConflictError(memory_id="private-id")
    idempotency = IdempotencyConflictError(idempotency_key="private-key")
    provider = ProviderParseError(schema_version="v1")
    stale = StaleEventError(
        event_timestamp=datetime(2026, 7, 29, tzinfo=UTC),
        watermark=datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert isinstance(capability, MemoryEngineError)
    assert capability.operation == "semantic_search"
    assert "vector_search" in str(capability)
    assert conflict.memory_id == "private-id"
    assert "private-id" not in str(conflict)
    assert idempotency.idempotency_key == "private-key"
    assert "private-key" not in str(idempotency)
    assert isinstance(provider, ProviderError)
    assert provider.schema_version == "v1"
    assert provider.reason_code == "schema_mismatch"
    assert stale.event_timestamp < stale.watermark
    assert "2026" not in str(stale)


def test_every_exported_domain_class_has_a_public_docstring() -> None:
    exported_classes = [
        value for name in domain.__all__ if inspect.isclass(value := getattr(domain, name, None))
    ]

    assert exported_classes
    assert all(inspect.getdoc(exported_class) for exported_class in exported_classes)
