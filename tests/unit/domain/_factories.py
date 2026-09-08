"""Typed factories shared by domain unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

from portable_memory_engine.domain import MemoryKind, MemoryRecord, MemoryScope

NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


def make_scope(
    *,
    subject_id: str = "subject-1",
    namespace: str = "default",
    tenant_id: str | None = "tenant-1",
) -> MemoryScope:
    """Return a valid complete scope."""

    return MemoryScope(
        subject_id=subject_id,
        namespace=namespace,
        tenant_id=tenant_id,
    )


def make_record(
    *,
    memory_id: str = "memory-1",
    scope: MemoryScope | None = None,
    content: str = "A durable fact",
) -> MemoryRecord:
    """Return a valid immutable record."""

    return MemoryRecord(
        id=memory_id,
        scope=scope or make_scope(),
        kind=MemoryKind.FACT,
        content=content,
        created_at=NOW,
        updated_at=NOW,
    )
