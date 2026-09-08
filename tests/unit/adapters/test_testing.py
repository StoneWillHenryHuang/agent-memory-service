"""Tests for deterministic provider, time, and identifier adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import pytest

from portable_memory_engine.adapters import (
    DeterministicEmbedder,
    DeterministicIdGenerator,
    FixedClock,
    ScriptedChatModel,
)
from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    EmbeddingTask,
    LifecycleError,
    ProviderError,
    ProviderUnavailableError,
)
from portable_memory_engine.ports import (
    ChatModel,
    ChatRequest,
    ChatResponse,
    Clock,
    Embedder,
    EmbeddingRequest,
    IdentifierPurpose,
    IdGenerator,
    TokenUsage,
)


def test_deterministic_embedder_is_stable_and_preserves_batch_order() -> None:
    async def scenario() -> None:
        embedder = DeterministicEmbedder(dimension=8, model_id="deterministic-test")
        assert isinstance(embedder, Embedder)
        with pytest.raises(LifecycleError):
            await embedder.embed(EmbeddingRequest(("same",), EmbeddingTask.DOCUMENT))
        await embedder.open()
        try:
            documents = await embedder.embed(
                EmbeddingRequest(("same", "different"), EmbeddingTask.DOCUMENT)
            )
            repeated = await embedder.embed(
                EmbeddingRequest(("same", "different"), EmbeddingTask.DOCUMENT)
            )
            query = await embedder.embed(EmbeddingRequest(("same",), EmbeddingTask.QUERY))
            assert documents == repeated
            assert documents[0].values == query[0].values
            assert documents[0].task is EmbeddingTask.DOCUMENT
            assert query[0].task is EmbeddingTask.QUERY
            assert documents[0].values != documents[1].values
            assert embedder.dimension == 8
            assert embedder.model_id == "deterministic-test"
            with pytest.raises(DomainValidationError, match="valid Unicode") as captured:
                await embedder.embed(EmbeddingRequest(("\ud800",), EmbeddingTask.DOCUMENT))
            assert captured.value.__cause__ is None
        finally:
            await embedder.close()

    asyncio.run(scenario())


def test_scripted_chat_model_consumes_public_outcomes_without_retaining_requests() -> None:
    async def scenario() -> None:
        response = ChatResponse(
            "synthetic response",
            "scripted-v1",
            TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3),
        )
        model = ScriptedChatModel((response, ProviderUnavailableError("synthetic unavailable")))
        assert isinstance(model, ChatModel)
        request = ChatRequest((ConversationMessage(role="user", content="synthetic request"),))
        with pytest.raises(LifecycleError):
            await model.complete(request)
        await model.open()
        try:
            assert await model.complete(request) == response
            with pytest.raises(ProviderUnavailableError, match="synthetic unavailable"):
                await model.complete(request)
            with pytest.raises(ProviderError, match="no remaining response"):
                await model.complete(request)
            assert model.call_count == 2
            assert model.remaining == 0
            assert not hasattr(model, "requests")
        finally:
            await model.close()

    asyncio.run(scenario())


def test_fixed_clock_normalizes_utc_and_rejects_naive_time() -> None:
    local = datetime(2026, 7, 30, 16, tzinfo=timezone(timedelta(hours=8)))
    clock = FixedClock(local)

    assert isinstance(clock, Clock)
    assert clock.now() == datetime(2026, 7, 30, 8, tzinfo=UTC)
    assert clock.now() is clock.now()
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        FixedClock(datetime(2026, 7, 30))


def test_deterministic_id_generator_is_thread_safe_and_per_purpose() -> None:
    generator = DeterministicIdGenerator(prefix="tests", start=1)
    assert isinstance(generator, IdGenerator)

    def generate_memory_id(_: int) -> str:
        return generator.new_id(purpose=IdentifierPurpose.MEMORY)

    with ThreadPoolExecutor(max_workers=8) as executor:
        identifiers = tuple(executor.map(generate_memory_id, range(100)))

    assert len(set(identifiers)) == 100
    assert set(identifiers) == {f"tests-memory-{index:06d}" for index in range(1, 101)}
    assert generator.new_id(purpose=IdentifierPurpose.COMMAND) == "tests-command-000001"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: DeterministicEmbedder(dimension=0),
        lambda: DeterministicEmbedder(model_id="bad model"),
        lambda: DeterministicIdGenerator(start=-1),
        lambda: DeterministicIdGenerator(prefix="bad prefix"),
        lambda: DeterministicIdGenerator(prefix="*"),
    ],
)
def test_deterministic_adapters_reject_invalid_configuration(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(DomainValidationError):
        factory()
