"""Recall reranking extension point with a dependency-free no-op default."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from portable_memory_engine.application.selection import RecallRequest
from portable_memory_engine.domain import DomainValidationError, MemoryMatch


@dataclass(frozen=True, slots=True)
class RerankRequest:
    """Scoped reranking input whose content-bearing candidates stay out of repr."""

    recall: RecallRequest
    candidates: Sequence[MemoryMatch] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.recall, RecallRequest):
            raise DomainValidationError("rerank recall must be RecallRequest")
        candidates = tuple(self.candidates)
        if any(not isinstance(candidate, MemoryMatch) for candidate in candidates):
            raise DomainValidationError("rerank candidates must contain MemoryMatch values")
        if any(candidate.record.scope != self.recall.query.scope for candidate in candidates):
            raise DomainValidationError("rerank candidate is outside the complete query scope")
        object.__setattr__(self, "candidates", candidates)


@runtime_checkable
class RerankStrategy(Protocol):
    """Replaceable asynchronous reranker over one bounded selected page."""

    async def rerank(self, request: RerankRequest) -> Sequence[MemoryMatch]:
        """Return reordered or narrowed candidates without changing scope."""

        ...


class NoOpReranker:
    """Default strategy preserving store order and semantic scores exactly."""

    async def rerank(self, request: RerankRequest) -> Sequence[MemoryMatch]:
        """Return the immutable selected candidates unchanged."""

        if not isinstance(request, RerankRequest):
            raise DomainValidationError("no-op reranker requires RerankRequest")
        return request.candidates
