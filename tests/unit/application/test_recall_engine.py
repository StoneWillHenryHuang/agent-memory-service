"""Synthetic recency, semantic, policy, pagination, and reranking recall tests."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

import pytest

from portable_memory_engine.adapters import DeterministicEmbedder, InMemoryMemoryStore
from portable_memory_engine.application import (
    RecallEngine,
    RecallLimits,
    RecallMode,
    RecallRequest,
    RerankRequest,
)
from portable_memory_engine.domain import (
    AccessDeniedError,
    CapabilityError,
    ConditionalWrite,
    CountPrecision,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    MemoryKind,
    MemoryMatch,
    MemoryPage,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
)
from portable_memory_engine.ports import (
    EmbeddingRequest,
    ObservationOutcome,
    StorageCapabilities,
)
from tests.unit.application._helpers import (
    PROCESSING_TIME,
    DenyAccessPolicy,
    RecordingObserver,
)

SCOPE = MemoryScope(subject_id="recall-subject", namespace="synthetic-recall")
OTHER_SCOPE = MemoryScope(subject_id="other-subject", namespace="synthetic-recall")


class CountingStore(InMemoryMemoryStore):
    """In-memory store recording whether recency selection was attempted."""

    def __init__(self) -> None:
        super().__init__()
        self.query_calls = 0

    async def query(self, query: MemoryQuery) -> MemoryPage:
        self.query_calls += 1
        return await super().query(query)


class RecencyOnlyStore(CountingStore):
    """Synthetic store declaring vector search unavailable."""

    @property
    def capabilities(self) -> StorageCapabilities:
        return replace(super().capabilities, vector_search=False)


class CountingEmbedder(DeterministicEmbedder):
    """Deterministic embedder recording provider calls without retaining text."""

    def __init__(self) -> None:
        super().__init__(dimension=8, model_id="recall-embedding-v1")
        self.calls = 0

    async def embed(self, request: EmbeddingRequest) -> tuple[Embedding, ...]:
        self.calls += 1
        return await super().embed(request)


class ReverseReranker:
    """Synthetic strategy proving store order can be replaced explicitly."""

    async def rerank(self, request: RerankRequest) -> Sequence[MemoryMatch]:
        return tuple(reversed(request.candidates))


class FabricatingReranker:
    """Invalid strategy attempting to inject an unselected record."""

    async def rerank(self, request: RerankRequest) -> Sequence[MemoryMatch]:
        candidate = request.candidates[0]
        fabricated = replace(candidate.record, id="fabricated-memory")
        return (MemoryMatch(fabricated, candidate.score),)


class StaticSelector:
    """Synthetic selector exposing adapter count-precision variants."""

    def __init__(self, page: MemoryPage) -> None:
        self._page = page

    async def select(self, request: RecallRequest) -> MemoryPage:
        return self._page


async def _put(
    *,
    store: InMemoryMemoryStore,
    embedder: DeterministicEmbedder,
    memory_id: str,
    scope: MemoryScope = SCOPE,
    kind: MemoryKind = MemoryKind.FACT,
    content: str,
    minutes: int,
    source: str = "synthetic-chat",
    session_id: str = "session-a",
) -> None:
    timestamp = PROCESSING_TIME + timedelta(minutes=minutes)
    embedding = (await embedder.embed(EmbeddingRequest((content,), EmbeddingTask.DOCUMENT)))[0]
    await store.put(
        ConditionalWrite(
            record=MemoryRecord(
                id=memory_id,
                scope=scope,
                kind=kind,
                content=content,
                created_at=PROCESSING_TIME,
                updated_at=timestamp,
                provenance=MemoryProvenance(
                    source=source,
                    session_id=session_id,
                    event_timestamp=timestamp,
                ),
                embedding=embedding,
            ),
            idempotency_key=f"seed-{memory_id}",
            payload_digest=f"digest-{memory_id}",
            event_timestamp=timestamp,
        )
    )


def test_recency_recall_pushes_scope_and_all_filters_into_store() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        embedder = DeterministicEmbedder(dimension=8, model_id="recall-embedding-v1")
        observer = RecordingObserver()
        engine = RecallEngine(store=store, embedder=embedder, observer=observer)
        async with engine:
            await _put(
                store=store,
                embedder=embedder,
                memory_id="fact-chat",
                content="Synthetic rail preference",
                minutes=1,
            )
            await _put(
                store=store,
                embedder=embedder,
                memory_id="summary-chat",
                kind=MemoryKind.SUMMARY,
                content="Synthetic session summary",
                minutes=2,
            )
            await _put(
                store=store,
                embedder=embedder,
                memory_id="fact-api",
                content="Synthetic API fact",
                minutes=3,
                source="synthetic-api",
                session_id="session-b",
            )
            await _put(
                store=store,
                embedder=embedder,
                memory_id="fact-api",
                scope=OTHER_SCOPE,
                content="Out of scope synthetic content",
                minutes=4,
                source="synthetic-api",
                session_id="session-b",
            )

            first = await engine.recall(
                RecallRequest(
                    MemoryQuery(
                        scope=SCOPE,
                        kinds=frozenset({MemoryKind.FACT}),
                        limit=1,
                    )
                )
            )
            second = await engine.recall(
                RecallRequest(
                    MemoryQuery(
                        scope=SCOPE,
                        kinds=frozenset({MemoryKind.FACT}),
                        offset=1,
                        limit=1,
                    )
                )
            )
            by_source = await engine.recall(
                RecallRequest(
                    MemoryQuery(
                        scope=SCOPE,
                        kinds=frozenset({MemoryKind.FACT}),
                        source="synthetic-chat",
                    )
                )
            )
            by_session = await engine.recall(
                RecallRequest(
                    MemoryQuery(
                        scope=SCOPE,
                        kinds=frozenset({MemoryKind.SUMMARY}),
                        session_id="session-a",
                    )
                )
            )

        assert [item.record.id for item in first.items] == ["fact-api"]
        assert first.page.total_count == 2
        assert first.page.count_precision is CountPrecision.EXACT
        assert first.page.next_offset == 1
        assert [item.record.id for item in second.items] == ["fact-chat"]
        assert second.page.next_offset is None
        assert [item.record.id for item in by_source.items] == ["fact-chat"]
        assert [item.record.id for item in by_session.items] == ["summary-chat"]
        assert all(
            item.record.scope == SCOPE for result in (first, second) for item in result.items
        )
        assert len(observer.events) == 4
        assert all(event.operation == "memory.recall.recency" for event in observer.events)
        assert all(not hasattr(event, "query") for event in observer.events)
        assert all(not hasattr(event, "content") for event in observer.events)

    asyncio.run(scenario())


def test_semantic_recall_embeds_once_and_preserves_store_side_filters() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        embedder = CountingEmbedder()
        observer = RecordingObserver()
        engine = RecallEngine(store=store, embedder=embedder, observer=observer)
        async with engine:
            await _put(
                store=store,
                embedder=embedder,
                memory_id="semantic-target",
                content="Morning train itinerary",
                minutes=1,
            )
            await _put(
                store=store,
                embedder=embedder,
                memory_id="wrong-session",
                content="Morning train itinerary",
                minutes=2,
                session_id="session-b",
            )
            await _put(
                store=store,
                embedder=embedder,
                memory_id="wrong-kind",
                kind=MemoryKind.SUMMARY,
                content="Morning train itinerary",
                minutes=3,
            )
            await _put(
                store=store,
                embedder=embedder,
                memory_id="semantic-target",
                scope=OTHER_SCOPE,
                content="Morning train itinerary",
                minutes=4,
            )
            seed_calls = embedder.calls
            result = await engine.recall(
                RecallRequest(
                    MemoryQuery(
                        scope=SCOPE,
                        kinds=frozenset({MemoryKind.FACT}),
                        source="synthetic-chat",
                        session_id="session-a",
                    ),
                    RecallMode.SEMANTIC,
                    "Morning train itinerary",
                    min_score=0.9,
                )
            )

        assert embedder.calls == seed_calls + 1
        assert [item.record.id for item in result.items] == ["semantic-target"]
        assert result.items[0].score == 1.0
        assert result.page.total_count == 1
        assert observer.events[0].operation == "memory.recall.semantic"
        assert observer.events[0].kind == MemoryKind.FACT

    asyncio.run(scenario())


def test_missing_vector_capability_fails_before_embedding_without_fallback() -> None:
    async def scenario() -> None:
        store = RecencyOnlyStore()
        embedder = CountingEmbedder()
        observer = RecordingObserver()
        engine = RecallEngine(store=store, embedder=embedder, observer=observer)
        async with engine:
            with pytest.raises(CapabilityError, match="vector_search"):
                await engine.recall(
                    RecallRequest(
                        MemoryQuery(SCOPE),
                        RecallMode.SEMANTIC,
                        "Synthetic semantic query",
                    )
                )

        assert embedder.calls == 0
        assert store.query_calls == 0
        assert observer.events[0].outcome is ObservationOutcome.FAILED

    asyncio.run(scenario())


def test_access_denial_happens_before_store_selection() -> None:
    async def scenario() -> None:
        store = CountingStore()
        observer = RecordingObserver()
        engine = RecallEngine(
            store=store,
            access_policy=DenyAccessPolicy(),
            observer=observer,
        )
        async with engine:
            with pytest.raises(AccessDeniedError):
                await engine.recall(RecallRequest(MemoryQuery(SCOPE)))

        assert store.query_calls == 0
        assert observer.events[0].outcome is ObservationOutcome.REJECTED

    asyncio.run(scenario())


def test_semantic_query_limit_fails_before_embedding() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        embedder = CountingEmbedder()
        engine = RecallEngine(
            store=store,
            embedder=embedder,
            limits=RecallLimits(max_semantic_query_characters=5),
        )
        async with engine:
            with pytest.raises(DomainValidationError, match="exceeds"):
                await engine.recall(
                    RecallRequest(
                        MemoryQuery(SCOPE),
                        RecallMode.SEMANTIC,
                        "too long",
                    )
                )

        assert embedder.calls == 0

    asyncio.run(scenario())


def test_reranker_can_reorder_but_cannot_fabricate_records() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        embedder = DeterministicEmbedder(dimension=8)
        reverse_engine = RecallEngine(
            store=store,
            embedder=embedder,
            reranker=ReverseReranker(),
        )
        async with reverse_engine:
            await _put(
                store=store,
                embedder=embedder,
                memory_id="older-record",
                content="Older synthetic content",
                minutes=1,
            )
            await _put(
                store=store,
                embedder=embedder,
                memory_id="newer-record",
                content="Newer synthetic content",
                minutes=2,
            )
            reversed_result = await reverse_engine.recall(
                RecallRequest(MemoryQuery(SCOPE, limit=2))
            )

        fabricated_store = InMemoryMemoryStore()
        fabricated_embedder = DeterministicEmbedder(dimension=8)
        fabricated_engine = RecallEngine(
            store=fabricated_store,
            embedder=fabricated_embedder,
            reranker=FabricatingReranker(),
        )
        async with fabricated_engine:
            await _put(
                store=fabricated_store,
                embedder=fabricated_embedder,
                memory_id="selected-memory",
                content="Selected synthetic content",
                minutes=1,
            )
            with pytest.raises(DomainValidationError, match="outside"):
                await fabricated_engine.recall(RecallRequest(MemoryQuery(SCOPE)))

        assert [item.record.id for item in reversed_result.items] == [
            "older-record",
            "newer-record",
        ]
        assert reversed_result.page.total_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("precision", "total"),
    [
        (CountPrecision.APPROXIMATE, 7),
        (CountPrecision.UNAVAILABLE, None),
    ],
)
def test_recall_preserves_adapter_count_precision(
    precision: CountPrecision,
    total: int | None,
) -> None:
    async def scenario() -> None:
        page = MemoryPage(
            scope=SCOPE,
            offset=10,
            limit=5,
            total_count=total,
            count_precision=precision,
        )
        engine = RecallEngine(
            store=InMemoryMemoryStore(),
            selector=StaticSelector(page),
        )
        async with engine:
            result = await engine.recall(RecallRequest(MemoryQuery(SCOPE, offset=10, limit=5)))

        assert result.items == ()
        assert result.page.count_precision is precision
        assert result.page.total_count == total
        assert result.page.offset == 10
        assert result.page.limit == 5

    asyncio.run(scenario())
