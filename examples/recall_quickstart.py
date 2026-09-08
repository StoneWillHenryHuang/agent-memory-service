"""Network-free recency and semantic recall over one synthetic memory."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from portable_memory_engine.adapters import (
    DeterministicEmbedder,
    FixedClock,
    InMemoryMemoryStore,
)
from portable_memory_engine.application import (
    RecallEngine,
    RecallMode,
    RecallRequest,
)
from portable_memory_engine.domain import (
    ConditionalWrite,
    EmbeddingTask,
    MemoryKind,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
)
from portable_memory_engine.ports import EmbeddingRequest


async def main() -> None:
    """Recall one local memory in both supported selection modes."""

    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    scope = MemoryScope(subject_id="example-subject", namespace="recall-quickstart")
    content = "The synthetic traveler prefers morning trains."
    store = InMemoryMemoryStore()
    embedder = DeterministicEmbedder(dimension=8, model_id="example-embedding-v1")
    engine = RecallEngine(
        store=store,
        embedder=embedder,
        clock=FixedClock(timestamp),
    )

    async with engine:
        document_embedding = (
            await embedder.embed(EmbeddingRequest((content,), EmbeddingTask.DOCUMENT))
        )[0]
        await store.put(
            ConditionalWrite(
                record=MemoryRecord(
                    id="example-fact-1",
                    scope=scope,
                    kind=MemoryKind.FACT,
                    content=content,
                    created_at=timestamp,
                    updated_at=timestamp,
                    provenance=MemoryProvenance(
                        source="example",
                        session_id="example-session-1",
                        event_timestamp=timestamp,
                    ),
                    embedding=document_embedding,
                ),
                idempotency_key="recall-example-seed-1",
                payload_digest="recall-example-digest-1",
                event_timestamp=timestamp,
            )
        )
        query = MemoryQuery(
            scope=scope,
            kinds=frozenset({MemoryKind.FACT}),
            source="example",
            session_id="example-session-1",
        )
        recency = await engine.recall(RecallRequest(query))
        semantic = await engine.recall(
            RecallRequest(
                query,
                mode=RecallMode.SEMANTIC,
                semantic_text=content,
            )
        )

    assert [item.record.id for item in recency.items] == ["example-fact-1"]
    assert [item.record.id for item in semantic.items] == ["example-fact-1"]
    assert semantic.items[0].score == 1.0


if __name__ == "__main__":
    asyncio.run(main())
