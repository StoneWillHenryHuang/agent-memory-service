"""Session contribution visibility and regeneration tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

from portable_memory_engine.adapters import InMemoryMemoryStore
from portable_memory_engine.domain import (
    ConditionalWrite,
    DeleteSessionCommand,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    VersionToken,
)
from tests.unit.adapters._factories import NOW, make_record


def _write(
    record: MemoryRecord,
    *,
    key: str,
    timestamp: datetime,
    expected_version: VersionToken | None = None,
) -> ConditionalWrite:
    return ConditionalWrite(
        record=record,
        idempotency_key=key,
        payload_digest=f"digest:{key}",
        event_timestamp=timestamp,
        expected_version=expected_version,
    )


def _from_session(
    record: MemoryRecord,
    session_id: str,
    timestamp: datetime,
) -> MemoryRecord:
    return replace(
        record,
        updated_at=timestamp,
        provenance=MemoryProvenance(
            source="synthetic",
            session_id=session_id,
            event_timestamp=timestamp,
        ),
    )


def test_deleted_shared_fact_stays_hidden_until_removed_contributor_regenerates() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            base = make_record("shared-fact")
            first = _from_session(base, "session-a", NOW)
            created = await store.put(_write(first, key="shared-a", timestamp=NOW))
            second_time = NOW + timedelta(seconds=1)
            second = _from_session(base, "session-b", second_time)
            await store.put(
                _write(
                    second,
                    key="shared-b",
                    timestamp=second_time,
                    expected_version=created.outcomes[0].version,
                )
            )

            deletion_time = NOW + timedelta(seconds=2)
            result = await store.delete(
                DeleteSessionCommand(
                    scope=base.scope,
                    session_id="session-a",
                    idempotency_key="delete-shared-a",
                    payload_digest="digest:delete-shared-a",
                    event_timestamp=deletion_time,
                )
            )
            assert result.contribution_deleted_count == 1
            assert result.hard_deleted_count == 0
            assert await store.get(scope=base.scope, memory_id=base.id) is None

            second_replay_time = NOW + timedelta(seconds=3)
            second_replay = _from_session(base, "session-b", second_replay_time)
            await store.put(
                _write(
                    second_replay,
                    key="shared-b-again",
                    timestamp=second_replay_time,
                )
            )
            assert await store.get(scope=base.scope, memory_id=base.id) is None

            regenerated_time = NOW + timedelta(seconds=4)
            regenerated = _from_session(base, "session-a", regenerated_time)
            await store.put(
                _write(
                    regenerated,
                    key="shared-a-regenerated",
                    timestamp=regenerated_time,
                )
            )
            assert await store.get(scope=base.scope, memory_id=base.id) is not None
        finally:
            await store.close()

    asyncio.run(scenario())


def test_profile_delete_only_removes_snapshot_owned_by_target_session() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            base = replace(make_record("profile"), kind=MemoryKind.PROFILE)
            first = _from_session(base, "session-a", NOW)
            created = await store.put(_write(first, key="profile-a", timestamp=NOW))
            later = NOW + timedelta(seconds=1)
            current = _from_session(base, "session-b", later)
            await store.put(
                _write(
                    current,
                    key="profile-b",
                    timestamp=later,
                    expected_version=created.outcomes[0].version,
                )
            )
            result = await store.delete(
                DeleteSessionCommand(
                    scope=base.scope,
                    session_id="session-a",
                    idempotency_key="delete-old-profile-session",
                    payload_digest="digest:delete-old-profile-session",
                    event_timestamp=NOW + timedelta(seconds=2),
                )
            )

            assert result.deleted_count == 0
            assert await store.get(scope=base.scope, memory_id=base.id) is not None
        finally:
            await store.close()

    asyncio.run(scenario())
