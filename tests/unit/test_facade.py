"""Synthetic end-to-end tests for the stable root MemoryEngine facade."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from portable_memory_engine import (
    AddMemoryCommand,
    AddResultStatus,
    ChatResponse,
    ConversationMessage,
    DefaultPromptProvider,
    DeleteMemoryCommand,
    DeterministicEmbedder,
    DomainValidationError,
    FixedClock,
    InMemoryMemoryStore,
    LifecycleError,
    MemoryEngine,
    MemoryKind,
    MemoryQuery,
    MemoryScope,
    RecallRequest,
    ScriptedChatModel,
)

EVENT_TIME = datetime(2026, 7, 31, 8, tzinfo=UTC)
SCOPE = MemoryScope(subject_id="synthetic-facade-subject", namespace="facade-test")


def _model() -> ScriptedChatModel:
    return ScriptedChatModel(
        (
            ChatResponse(
                content='{"schema_version":"fact.v1","facts":["Uses synthetic rail"]}',
                model_id="facade-model-v1",
            ),
            ChatResponse(
                content=(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"add","memory_id":null,"content":"Uses synthetic rail"}]}'
                ),
                model_id="facade-model-v1",
            ),
        )
    )


def _command() -> AddMemoryCommand:
    return AddMemoryCommand(
        scope=SCOPE,
        messages=(ConversationMessage("user", "I use synthetic rail"),),
        idempotency_key="facade-add-1",
        event_timestamp=EVENT_TIME,
        kinds=frozenset({MemoryKind.FACT}),
        source="facade-test",
        session_id="facade-session-1",
    )


def test_facade_adds_recalls_deletes_and_owns_shared_resources() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        engine = MemoryEngine(
            store=store,
            chat_model=_model(),
            embedder=DeterministicEmbedder(dimension=8),
            prompt_provider=DefaultPromptProvider(),
            clock=FixedClock(EVENT_TIME),
        )

        with pytest.raises(LifecycleError):
            await engine.add(_command())

        async with engine:
            added = await engine.add(_command())
            recalled = await engine.recall(RecallRequest(MemoryQuery(scope=SCOPE)))
            memory_id = recalled.items[0].record.id
            deleted = await engine.delete(
                DeleteMemoryCommand(
                    scope=SCOPE,
                    memory_id=memory_id,
                    idempotency_key="facade-delete-1",
                    payload_digest="facade-delete-digest-1",
                    event_timestamp=EVENT_TIME + timedelta(minutes=1),
                )
            )
            empty = await engine.recall(RecallRequest(MemoryQuery(scope=SCOPE)))

        assert added.status is AddResultStatus.SUCCEEDED
        assert [match.record.content for match in recalled.items] == ["Uses synthetic rail"]
        assert deleted.hard_deleted_count == 1
        assert empty.items == ()
        with pytest.raises(LifecycleError):
            await store.query(MemoryQuery(scope=SCOPE))
        with pytest.raises(LifecycleError):
            await engine.recall(RecallRequest(MemoryQuery(scope=SCOPE)))

    asyncio.run(scenario())


def test_facade_can_leave_injected_resource_lifecycle_to_caller() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        model = _model()
        embedder = DeterministicEmbedder(dimension=8)
        prompts = DefaultPromptProvider()
        engine = MemoryEngine(
            store=store,
            chat_model=model,
            embedder=embedder,
            prompt_provider=prompts,
            clock=FixedClock(EVENT_TIME),
            owns_resources=False,
        )
        await store.open()
        await model.open()
        await embedder.open()
        await prompts.open()
        try:
            async with engine:
                result = await engine.recall(RecallRequest(MemoryQuery(scope=SCOPE)))
                assert result.items == ()
            assert (await store.query(MemoryQuery(scope=SCOPE))).items == ()
        finally:
            await prompts.close()
            await embedder.close()
            await model.close()
            await store.close()

    asyncio.run(scenario())


def test_facade_rejects_ambiguous_lifecycle_flag() -> None:
    with pytest.raises(DomainValidationError, match="owns_resources"):
        MemoryEngine(
            store=InMemoryMemoryStore(),
            chat_model=_model(),
            embedder=DeterministicEmbedder(dimension=8),
            prompt_provider=DefaultPromptProvider(),
            owns_resources=1,  # type: ignore[arg-type]
        )
