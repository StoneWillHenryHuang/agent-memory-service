"""Provider-neutral recency and semantic recall selection strategies."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from portable_memory_engine.domain import (
    CapabilityError,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    MemoryPage,
    MemoryQuery,
    ProviderError,
    SemanticQuery,
)
from portable_memory_engine.ports import (
    Embedder,
    EmbeddingRequest,
    MemoryStore,
    StorageCapability,
)


class RecallMode(StrEnum):
    """Stable selection mode for a recall request."""

    RECENCY = "recency"
    SEMANTIC = "semantic"


def _semantic_text(value: str | None) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("semantic recall requires query text")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized.strip() or "\x00" in normalized:
        raise DomainValidationError("semantic query text must be non-empty without NUL")
    return normalized


def _score(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainValidationError("recall min_score must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise DomainValidationError("recall min_score must be between 0 and 1")
    return normalized


@dataclass(frozen=True, slots=True)
class RecallRequest:
    """One application recall request built from a complete-scope domain query."""

    query: MemoryQuery
    mode: RecallMode = RecallMode.RECENCY
    semantic_text: str | None = field(default=None, repr=False)
    min_score: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, MemoryQuery):
            raise DomainValidationError("recall query must be MemoryQuery")
        if not isinstance(self.mode, RecallMode):
            raise DomainValidationError("recall mode must be RecallMode")
        if self.mode is RecallMode.RECENCY:
            if self.semantic_text is not None or self.min_score is not None:
                raise DomainValidationError(
                    "recency recall cannot include semantic text or min_score"
                )
            return
        object.__setattr__(self, "semantic_text", _semantic_text(self.semantic_text))
        object.__setattr__(self, "min_score", _score(self.min_score))


@runtime_checkable
class RecallSelectionStrategy(Protocol):
    """Replaceable strategy that returns one already scoped domain page."""

    async def select(self, request: RecallRequest) -> MemoryPage:
        """Select a page or raise a sanitized domain, provider, or store error."""

        ...


class StoreRecallSelector:
    """Default selector delegating all filters and ordering to ``MemoryStore``."""

    def __init__(self, *, store: MemoryStore, embedder: Embedder | None) -> None:
        self._store = store
        self._embedder = embedder

    async def select(self, request: RecallRequest) -> MemoryPage:
        """Run recency directly or embed once before native semantic search."""

        if not isinstance(request, RecallRequest):
            raise DomainValidationError("selection requires RecallRequest")
        if request.mode is RecallMode.RECENCY:
            return await self._store.query(request.query)

        self._store.capabilities.require(
            operation="memory.recall.semantic",
            capabilities=(StorageCapability.VECTOR_SEARCH,),
        )
        if self._embedder is None:
            raise CapabilityError(
                operation="memory.recall.semantic",
                capability="query_embedding_provider",
            )
        semantic_text = request.semantic_text
        if semantic_text is None:
            raise DomainValidationError("semantic recall requires query text")
        embeddings = await self._embedder.embed(
            EmbeddingRequest((semantic_text,), EmbeddingTask.QUERY)
        )
        if len(embeddings) != 1 or not isinstance(embeddings[0], Embedding):
            raise ProviderError("embedder returned an unexpected result count")
        query = request.query
        return await self._store.semantic_search(
            SemanticQuery(
                scope=query.scope,
                text=semantic_text,
                kinds=query.kinds,
                source=query.source,
                session_id=query.session_id,
                offset=query.offset,
                limit=query.limit,
                min_score=request.min_score,
            ),
            query_embedding=embeddings[0],
        )
