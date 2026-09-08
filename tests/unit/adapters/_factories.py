"""Synthetic values shared by in-memory adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime

from portable_memory_engine.domain import (
    ConditionalWrite,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    MemoryScope,
)

NOW = datetime(2026, 7, 30, 8, tzinfo=UTC)


def make_scope(subject: str = "subject-1") -> MemoryScope:
    """Return a complete synthetic scope."""

    return MemoryScope(tenant_id="tenant-1", subject_id=subject, namespace="tests")


def make_record(
    memory_id: str,
    *,
    scope: MemoryScope | None = None,
    timestamp: datetime = NOW,
    content: str | None = None,
) -> MemoryRecord:
    """Return one synthetic fact record."""

    return MemoryRecord(
        id=memory_id,
        scope=scope or make_scope(),
        kind=MemoryKind.FACT,
        content=content or f"content-{memory_id}",
        created_at=timestamp,
        updated_at=timestamp,
        provenance=MemoryProvenance(source="tests", event_timestamp=timestamp),
    )


def make_write(
    record: MemoryRecord,
    *,
    key: str,
    timestamp: datetime | None = None,
    expected_version: str | int | None = None,
) -> ConditionalWrite:
    """Return one deterministic conditional command."""

    return ConditionalWrite(
        record=record,
        idempotency_key=key,
        payload_digest=f"digest:{key}",
        event_timestamp=timestamp or record.updated_at,
        expected_version=expected_version,
    )
