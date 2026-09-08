"""Tests for scoped queries and pagination invariants."""

from __future__ import annotations

from typing import cast

import pytest

from portable_memory_engine.domain import (
    CountPrecision,
    DomainValidationError,
    MemoryKind,
    MemoryMatch,
    MemoryPage,
    MemoryQuery,
    MemoryScope,
    SemanticQuery,
    SortOrder,
)
from tests.unit.domain._factories import make_record, make_scope


def test_memory_query_freezes_filters_and_keeps_source_outside_scope() -> None:
    query = MemoryQuery(
        scope=make_scope(),
        kinds=frozenset({MemoryKind.FACT, MemoryKind.PROFILE}),
        source="api",
        session_id="session-1",
        offset=10,
        limit=25,
        order=SortOrder.OLDEST,
    )

    assert query.kinds == frozenset({MemoryKind.FACT, MemoryKind.PROFILE})
    assert query.source == "api"
    assert not hasattr(query.scope, "source")
    assert query.offset == 10


@pytest.mark.parametrize(
    ("offset", "limit"),
    [(-1, 20), (0, 0), (0, 1_001), (cast("int", True), 20)],
)
def test_memory_query_rejects_invalid_pagination(offset: int, limit: int) -> None:
    with pytest.raises(DomainValidationError):
        MemoryQuery(scope=make_scope(), offset=offset, limit=limit)


def test_memory_query_rejects_invalid_scope_order_and_kind() -> None:
    with pytest.raises(DomainValidationError, match="scope"):
        MemoryQuery(scope=cast("MemoryScope", object()))
    with pytest.raises(DomainValidationError, match="SortOrder"):
        MemoryQuery(scope=make_scope(), order=cast("SortOrder", "newest"))
    with pytest.raises(DomainValidationError, match="MemoryKind"):
        MemoryQuery(scope=make_scope(), kinds=cast("frozenset[MemoryKind]", {"fact"}))


def test_semantic_query_validates_text_filters_and_score() -> None:
    query = SemanticQuery(
        scope=make_scope(),
        text="remember this",
        kinds=frozenset({MemoryKind.SUMMARY}),
        min_score=0.75,
    )

    assert query.text == "remember this"
    assert query.kinds == frozenset({MemoryKind.SUMMARY})
    assert query.min_score == 0.75
    assert "remember this" not in repr(query)


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), cast("float", True)])
def test_semantic_query_rejects_invalid_scores(score: float) -> None:
    with pytest.raises(DomainValidationError):
        SemanticQuery(scope=make_scope(), text="query", min_score=score)


def test_memory_match_validates_record_and_score() -> None:
    match = MemoryMatch(record=make_record(), score=1)

    assert match.score == 1.0
    with pytest.raises(DomainValidationError, match="record"):
        MemoryMatch(record=object())  # type: ignore[arg-type]


def test_memory_page_copies_items_and_supports_exact_counts() -> None:
    items = [MemoryMatch(make_record())]
    page = MemoryPage(
        scope=make_scope(),
        items=items,
        offset=0,
        limit=10,
        next_offset=1,
        total_count=1,
        count_precision=CountPrecision.EXACT,
    )
    items.clear()

    assert len(page.items) == 1
    assert isinstance(page.items, tuple)
    assert page.total_count == 1


def test_memory_page_defaults_to_an_unavailable_count() -> None:
    page = MemoryPage(scope=make_scope())

    assert page.total_count is None
    assert page.count_precision is CountPrecision.UNAVAILABLE


def test_memory_page_rejects_cross_scope_records() -> None:
    page_scope = make_scope(subject_id="subject-a")
    other_scope = make_scope(subject_id="subject-b")

    with pytest.raises(DomainValidationError, match="complete scope"):
        MemoryPage(
            scope=page_scope,
            items=(MemoryMatch(make_record(scope=other_scope)),),
        )


def test_same_memory_id_can_appear_in_separate_scoped_pages() -> None:
    first_scope = make_scope(subject_id="subject-a")
    second_scope = make_scope(subject_id="subject-b")

    first = MemoryPage(
        scope=first_scope,
        items=(MemoryMatch(make_record(scope=first_scope)),),
    )
    second = MemoryPage(
        scope=second_scope,
        items=(MemoryMatch(make_record(scope=second_scope)),),
    )

    assert first.items[0].record.id == second.items[0].record.id
    assert first.items[0].record.scope != second.items[0].record.scope


def test_memory_page_rejects_inconsistent_pagination_and_counts() -> None:
    match = MemoryMatch(make_record())
    with pytest.raises(DomainValidationError, match="more items"):
        MemoryPage(scope=make_scope(), items=(match, match), limit=1)
    with pytest.raises(DomainValidationError, match="next_offset"):
        MemoryPage(scope=make_scope(), items=(match,), next_offset=0)
    with pytest.raises(DomainValidationError, match="next_offset"):
        MemoryPage(scope=make_scope(), next_offset=0)
    with pytest.raises(DomainValidationError, match="total_count"):
        MemoryPage(
            scope=make_scope(),
            items=(match,),
            total_count=0,
            count_precision=CountPrecision.EXACT,
        )
    with pytest.raises(DomainValidationError, match="requires total_count"):
        MemoryPage(scope=make_scope(), total_count=1)
    with pytest.raises(DomainValidationError, match="requires a total_count"):
        MemoryPage(scope=make_scope(), count_precision=CountPrecision.EXACT)
    with pytest.raises(DomainValidationError, match="cannot precede"):
        MemoryPage(
            scope=make_scope(),
            items=(match,),
            offset=10,
            total_count=10,
            count_precision=CountPrecision.EXACT,
        )
