"""Recall request, result, selector, and reranker value tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

from portable_memory_engine.application import (
    NoOpReranker,
    RecallLimits,
    RecallMode,
    RecallRequest,
    RecallResult,
    RerankRequest,
)
from portable_memory_engine.domain import (
    CountPrecision,
    DomainValidationError,
    MemoryKind,
    MemoryMatch,
    MemoryPage,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
)

SCOPE = MemoryScope(subject_id="synthetic-recall-subject", namespace="recall-tests")
NOW = datetime(2026, 7, 31, 8, tzinfo=UTC)


def _record(memory_id: str = "synthetic-memory-1") -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=SCOPE,
        kind=MemoryKind.FACT,
        content="Synthetic recall content",
        created_at=NOW,
        updated_at=NOW,
        provenance=MemoryProvenance(source="synthetic-chat", session_id="session-1"),
    )


def test_recall_request_uses_domain_query_and_hides_semantic_text() -> None:
    query = MemoryQuery(
        scope=SCOPE,
        kinds=frozenset({MemoryKind.FACT}),
        source="synthetic-chat",
        session_id="session-1",
        offset=2,
        limit=4,
    )
    request = RecallRequest(
        query=query,
        mode=RecallMode.SEMANTIC,
        semantic_text="Synthetic private search text",
        min_score=0.25,
    )

    assert request.query is query
    assert request.semantic_text == "Synthetic private search text"
    assert request.min_score == 0.25
    assert "Synthetic private search text" not in repr(request)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RecallRequest(cast("MemoryQuery", object())),
        lambda: RecallRequest(MemoryQuery(SCOPE), cast("RecallMode", "semantic")),
        lambda: RecallRequest(MemoryQuery(SCOPE), semantic_text="unexpected"),
        lambda: RecallRequest(MemoryQuery(SCOPE), min_score=0.5),
        lambda: RecallRequest(MemoryQuery(SCOPE), RecallMode.SEMANTIC),
        lambda: RecallRequest(MemoryQuery(SCOPE), RecallMode.SEMANTIC, "  "),
        lambda: RecallRequest(MemoryQuery(SCOPE), RecallMode.SEMANTIC, "query", -0.1),
        lambda: RecallRequest(MemoryQuery(SCOPE), RecallMode.SEMANTIC, "query", 1.1),
        lambda: RecallLimits(max_semantic_query_characters=0),
        lambda: RecallLimits(max_semantic_query_characters=True),
    ],
)
def test_recall_values_reject_invalid_configuration(factory: Callable[[], object]) -> None:
    with pytest.raises(DomainValidationError):
        factory()


def test_recall_result_preserves_page_precision_and_hides_items() -> None:
    page = MemoryPage(
        scope=SCOPE,
        items=(MemoryMatch(_record()),),
        offset=0,
        limit=2,
        next_offset=1,
        total_count=8,
        count_precision=CountPrecision.APPROXIMATE,
    )
    result = RecallResult(RecallMode.RECENCY, page)

    assert result.items == page.items
    assert result.page.count_precision is CountPrecision.APPROXIMATE
    assert result.page.total_count == 8
    assert "Synthetic recall content" not in repr(result)
    assert "synthetic-memory-1" not in repr(result)


def test_empty_recall_result_shape_is_stable_for_unavailable_count() -> None:
    result = RecallResult(
        RecallMode.RECENCY,
        MemoryPage(
            scope=SCOPE,
            offset=20,
            limit=10,
            total_count=None,
            count_precision=CountPrecision.UNAVAILABLE,
        ),
    )

    assert result.items == ()
    assert result.page.offset == 20
    assert result.page.limit == 10
    assert result.page.next_offset is None
    assert result.page.total_count is None


def test_noop_reranker_is_async_and_preserves_candidate_identity() -> None:
    async def scenario() -> None:
        recall = RecallRequest(MemoryQuery(SCOPE))
        candidates = (MemoryMatch(_record()),)
        request = RerankRequest(recall, candidates)

        result = await NoOpReranker().rerank(request)

        assert result is request.candidates
        assert "Synthetic recall content" not in repr(request)

    asyncio.run(scenario())


def test_rerank_request_rejects_cross_scope_candidate() -> None:
    other = MemoryScope(subject_id="other-subject", namespace="recall-tests")
    record = MemoryRecord(
        id="other-record",
        scope=other,
        kind=MemoryKind.FACT,
        content="Other synthetic content",
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(DomainValidationError, match="outside"):
        RerankRequest(
            RecallRequest(MemoryQuery(SCOPE)),
            (MemoryMatch(record),),
        )
