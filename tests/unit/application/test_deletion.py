"""Deletion application-service authorization and observation tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from portable_memory_engine.adapters import InMemoryMemoryStore
from portable_memory_engine.application import DeleteEngine
from portable_memory_engine.domain import (
    AccessDeniedError,
    CapabilityError,
    ConditionalWrite,
    DeleteCommand,
    DeleteMemoryCommand,
    DeleteResult,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
)
from portable_memory_engine.ports import StorageCapabilities
from tests.unit.application._helpers import (
    EVENT_TIME,
    PROCESSING_TIME,
    SCOPE,
    DenyAccessPolicy,
    RecordingObserver,
)


class CountingStore(InMemoryMemoryStore):
    """In-memory adapter recording whether deletion reached storage."""

    def __init__(self) -> None:
        super().__init__()
        self.delete_calls = 0

    async def delete(self, command: DeleteCommand) -> DeleteResult:
        self.delete_calls += 1
        return await super().delete(command)


class NoSessionStore(CountingStore):
    """Store explicitly lacking the optional contribution capability."""

    @property
    def capabilities(self) -> StorageCapabilities:
        return replace(super().capabilities, session_contributions=False)


def _memory(memory_id: str = "delete-target") -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=SCOPE,
        kind=MemoryKind.FACT,
        content="Synthetic deletion target",
        created_at=EVENT_TIME,
        updated_at=EVENT_TIME,
        provenance=MemoryProvenance(
            source="synthetic",
            session_id="delete-session",
            event_timestamp=EVENT_TIME,
        ),
    )


def _key(index: int) -> str:
    return f"delete-command-{index}"


def _digest(index: int) -> str:
    return f"digest:delete-command-{index}"


def _time(index: int) -> datetime:
    return PROCESSING_TIME + timedelta(seconds=index)


def test_delete_engine_hard_deletes_and_emits_content_free_event() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        observer = RecordingObserver()
        engine = DeleteEngine(store=store, observer=observer)
        record = _memory()
        async with engine:
            await store.put(
                ConditionalWrite(
                    record=record,
                    idempotency_key="seed-delete-target",
                    payload_digest="digest:seed-delete-target",
                    event_timestamp=EVENT_TIME,
                )
            )
            result = await engine.delete(
                DeleteMemoryCommand(
                    scope=SCOPE,
                    memory_id=record.id,
                    idempotency_key=_key(1),
                    payload_digest=_digest(1),
                    event_timestamp=_time(1),
                )
            )

        assert result.hard_deleted_count == 1
        assert result.contribution_deleted_count == 0
        assert observer.events[0].operation == "memory.delete.memory_id"
        assert observer.events[0].item_count == 1
        assert not hasattr(observer.events[0], "memory_id")
        assert not hasattr(observer.events[0], "session_id")

    asyncio.run(scenario())


def test_delete_engine_authorizes_before_store_mutation() -> None:
    async def scenario() -> None:
        store = CountingStore()
        engine = DeleteEngine(store=store, access_policy=DenyAccessPolicy())
        async with engine:
            with pytest.raises(AccessDeniedError):
                await engine.delete(
                    DeleteScopeCommand(
                        scope=SCOPE,
                        idempotency_key=_key(2),
                        payload_digest=_digest(2),
                        event_timestamp=_time(2),
                    )
                )
        assert store.delete_calls == 0

    asyncio.run(scenario())


def test_delete_engine_rejects_missing_session_capability_before_store() -> None:
    async def scenario() -> None:
        store = NoSessionStore()
        engine = DeleteEngine(store=store)
        async with engine:
            with pytest.raises(CapabilityError, match="session_contributions"):
                await engine.delete(
                    DeleteSessionCommand(
                        scope=SCOPE,
                        session_id="delete-session",
                        idempotency_key=_key(3),
                        payload_digest=_digest(3),
                        event_timestamp=_time(3),
                    )
                )
        assert store.delete_calls == 0

    asyncio.run(scenario())


def test_delete_engine_accepts_all_four_typed_targets() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        observer = RecordingObserver()
        engine = DeleteEngine(store=store, observer=observer)
        commands = (
            DeleteMemoryCommand(
                scope=SCOPE,
                memory_id="absent",
                idempotency_key=_key(4),
                payload_digest=_digest(4),
                event_timestamp=_time(4),
            ),
            DeleteScopeCommand(
                scope=SCOPE,
                idempotency_key=_key(5),
                payload_digest=_digest(5),
                event_timestamp=_time(5),
            ),
            DeleteSubjectCommand(
                subject=SCOPE.subject,
                idempotency_key=_key(6),
                payload_digest=_digest(6),
                event_timestamp=_time(6),
            ),
            DeleteSessionCommand(
                scope=SCOPE,
                session_id="absent-session",
                idempotency_key=_key(7),
                payload_digest=_digest(7),
                event_timestamp=_time(7),
            ),
        )
        async with engine:
            results = [await engine.delete(command) for command in commands]

        assert [result.deleted_count for result in results] == [0, 0, 0, 0]
        assert [event.operation for event in observer.events] == [
            "memory.delete.memory_id",
            "memory.delete.scope",
            "memory.delete.subject",
            "memory.delete.session",
        ]

    asyncio.run(scenario())
