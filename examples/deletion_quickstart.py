"""Network-free session contribution deletion over synthetic memories."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from portable_memory_engine.adapters import InMemoryMemoryStore
from portable_memory_engine.application import DeleteEngine
from portable_memory_engine.domain import (
    ConditionalWrite,
    DeleteSessionCommand,
    MemoryKind,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
)


async def main() -> None:
    """Delete summary/fact contributions and one profile snapshot."""

    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    deletion_time = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    scope = MemoryScope(subject_id="example-subject", namespace="deletion-example")
    session_id = "example-session"
    store = InMemoryMemoryStore()
    engine = DeleteEngine(store=store)

    async with engine:
        for index, kind in enumerate(
            (MemoryKind.SUMMARY, MemoryKind.FACT, MemoryKind.PROFILE),
            start=1,
        ):
            record = MemoryRecord(
                id=f"example-{kind.value}",
                scope=scope,
                kind=kind,
                content=f"Synthetic {kind.value} content",
                created_at=event_time,
                updated_at=event_time,
                provenance=MemoryProvenance(
                    source="example",
                    session_id=session_id,
                    event_timestamp=event_time,
                ),
            )
            await store.put(
                ConditionalWrite(
                    record=record,
                    idempotency_key=f"deletion-example-seed-{index}",
                    payload_digest=f"deletion-example-digest-{index}",
                    event_timestamp=event_time,
                )
            )

        result = await engine.delete(
            DeleteSessionCommand(
                scope=scope,
                session_id=session_id,
                idempotency_key="deletion-example-command",
                payload_digest="deletion-example-command-digest",
                event_timestamp=deletion_time,
            )
        )
        remaining = await store.query(MemoryQuery(scope=scope))

    assert result.hard_deleted_count == 1
    assert result.contribution_deleted_count == 2
    assert result.tombstoned_count == 2
    assert result.deleted_count == 3
    assert remaining.items == ()


if __name__ == "__main__":
    asyncio.run(main())
