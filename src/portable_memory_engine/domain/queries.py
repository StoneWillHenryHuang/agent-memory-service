"""Scoped recall queries and stable page result values."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from portable_memory_engine.domain._validation import (
    normalize_content,
    normalize_kinds,
    normalize_optional_identifier,
    normalize_score,
    validate_pagination,
)
from portable_memory_engine.domain.enums import CountPrecision, MemoryKind, SortOrder
from portable_memory_engine.domain.errors import DomainValidationError
from portable_memory_engine.domain.models import MemoryRecord, MemoryScope


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """A complete-scope recency query with store-side filters."""

    scope: MemoryScope
    kinds: frozenset[MemoryKind] = field(default_factory=frozenset)
    source: str | None = field(default=None, repr=False)
    session_id: str | None = field(default=None, repr=False)
    offset: int = 0
    limit: int = 20
    order: SortOrder = SortOrder.NEWEST

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        if not isinstance(self.order, SortOrder):
            raise DomainValidationError("order must be a SortOrder")
        validate_pagination(offset=self.offset, limit=self.limit)
        object.__setattr__(self, "kinds", normalize_kinds(self.kinds))
        object.__setattr__(
            self,
            "source",
            normalize_optional_identifier(self.source, field_name="source", max_length=128),
        )
        object.__setattr__(
            self,
            "session_id",
            normalize_optional_identifier(self.session_id, field_name="session_id"),
        )


@dataclass(frozen=True, slots=True)
class SemanticQuery:
    """A complete-scope semantic query whose text is embedded by the application."""

    scope: MemoryScope
    text: str = field(repr=False)
    kinds: frozenset[MemoryKind] = field(default_factory=frozenset)
    source: str | None = field(default=None, repr=False)
    session_id: str | None = field(default=None, repr=False)
    offset: int = 0
    limit: int = 20
    min_score: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        validate_pagination(offset=self.offset, limit=self.limit)
        object.__setattr__(self, "text", normalize_content(self.text, field_name="semantic query"))
        object.__setattr__(self, "kinds", normalize_kinds(self.kinds))
        object.__setattr__(
            self,
            "source",
            normalize_optional_identifier(self.source, field_name="source", max_length=128),
        )
        object.__setattr__(
            self,
            "session_id",
            normalize_optional_identifier(self.session_id, field_name="session_id"),
        )
        object.__setattr__(
            self,
            "min_score",
            normalize_score(self.min_score, field_name="min_score"),
        )


@dataclass(frozen=True, slots=True)
class MemoryMatch:
    """One recalled record with an optional normalized semantic score."""

    record: MemoryRecord
    score: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.record, MemoryRecord):
            raise DomainValidationError("record must be a MemoryRecord")
        object.__setattr__(self, "score", normalize_score(self.score, field_name="score"))


@dataclass(frozen=True, slots=True)
class MemoryPage:
    """A stable page that rejects records outside its declared scope."""

    scope: MemoryScope
    items: Sequence[MemoryMatch] = ()
    offset: int = 0
    limit: int = 20
    next_offset: int | None = None
    total_count: int | None = None
    count_precision: CountPrecision = CountPrecision.UNAVAILABLE

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        if not isinstance(self.count_precision, CountPrecision):
            raise DomainValidationError("count_precision must be CountPrecision")
        validate_pagination(offset=self.offset, limit=self.limit)

        items = tuple(self.items)
        if any(not isinstance(item, MemoryMatch) for item in items):
            raise DomainValidationError("page items must contain MemoryMatch values")
        if len(items) > self.limit:
            raise DomainValidationError("page contains more items than its limit")
        if any(item.record.scope != self.scope for item in items):
            raise DomainValidationError("page contains a record outside its complete scope")

        if self.next_offset is not None and (
            isinstance(self.next_offset, bool)
            or not isinstance(self.next_offset, int)
            or self.next_offset <= self.offset
            or self.next_offset < self.offset + len(items)
        ):
            raise DomainValidationError("next_offset must follow the current page")

        if self.total_count is not None and (
            isinstance(self.total_count, bool)
            or not isinstance(self.total_count, int)
            or self.total_count < len(items)
        ):
            raise DomainValidationError("total_count must be a non-negative page total")

        if self.count_precision is CountPrecision.UNAVAILABLE and self.total_count is not None:
            raise DomainValidationError("unavailable count precision requires total_count=None")
        if self.count_precision is not CountPrecision.UNAVAILABLE and self.total_count is None:
            raise DomainValidationError("known count precision requires a total_count")
        if (
            self.count_precision is CountPrecision.EXACT
            and self.total_count is not None
            and items
            and self.total_count < self.offset + len(items)
        ):
            raise DomainValidationError("exact total_count cannot precede the current page")

        object.__setattr__(self, "items", items)
