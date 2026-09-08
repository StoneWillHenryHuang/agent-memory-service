"""Deterministic, network-free adapters for tests and local examples."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime

from portable_memory_engine.domain import (
    DomainValidationError,
    Embedding,
    LifecycleError,
    ProviderError,
)
from portable_memory_engine.ports import (
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    IdentifierPurpose,
)


def _identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be an identifier")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > 255
        or normalized == "*"
        or normalized != normalized.strip()
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in normalized
        )
    ):
        raise DomainValidationError(f"{field_name} must be an identifier")
    return normalized


class DeterministicEmbedder:
    """Hash-based embedder with stable output and no semantic-quality claim."""

    def __init__(self, *, dimension: int = 16, model_id: str = "deterministic-v1") -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            raise DomainValidationError("embedding dimension must be a positive integer")
        self._dimension = dimension
        self._model_id = _identifier(model_id, field_name="embedding model_id")
        self._lock = asyncio.Lock()
        self._open = False

    @property
    def dimension(self) -> int:
        """Return the configured deterministic vector dimension."""

        return self._dimension

    @property
    def model_id(self) -> str:
        """Return the stable model identity attached to generated embeddings."""

        return self._model_id

    async def open(self) -> None:
        """Open the adapter without performing I/O."""

        async with self._lock:
            self._open = True

    async def close(self) -> None:
        """Close the adapter; repeated calls are safe."""

        async with self._lock:
            self._open = False

    def _embed_text(self, text: str) -> tuple[float, ...]:
        try:
            payload = f"{self._model_id}\x00{text}".encode()
        except UnicodeEncodeError:
            raise DomainValidationError("embedding text must be valid Unicode") from None
        digest = hashlib.shake_256(payload).digest(self._dimension * 4)
        maximum = float((1 << 32) - 1)
        return tuple(
            (int.from_bytes(digest[index : index + 4], "big") / maximum) * 2.0 - 1.0
            for index in range(0, len(digest), 4)
        )

    async def embed(self, request: EmbeddingRequest) -> tuple[Embedding, ...]:
        """Return stable vectors in input order; no network is accessed."""

        async with self._lock:
            if not self._open:
                raise LifecycleError("deterministic embedder is not open")
            return tuple(
                Embedding(
                    values=self._embed_text(text),
                    model_id=self._model_id,
                    task=request.task,
                )
                for text in request.texts
            )


class ScriptedChatModel:
    """Chat adapter that returns or raises a fixed sequence of public outcomes."""

    def __init__(self, script: Sequence[ChatResponse | ProviderError]) -> None:
        steps = tuple(script)
        if any(not isinstance(step, (ChatResponse, ProviderError)) for step in steps):
            raise DomainValidationError("chat script must contain responses or provider errors")
        self._steps = steps
        self._index = 0
        self._lock = asyncio.Lock()
        self._open = False

    @property
    def call_count(self) -> int:
        """Return how many scripted calls have been consumed."""

        return self._index

    @property
    def remaining(self) -> int:
        """Return the number of unconsumed scripted outcomes."""

        return len(self._steps) - self._index

    async def open(self) -> None:
        """Open the model without creating a client or reading configuration."""

        async with self._lock:
            self._open = True

    async def close(self) -> None:
        """Close the model; repeated calls are safe."""

        async with self._lock:
            self._open = False

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Consume one outcome without retaining request content."""

        if not isinstance(request, ChatRequest):
            raise DomainValidationError("chat request must be ChatRequest")
        async with self._lock:
            if not self._open:
                raise LifecycleError("scripted chat model is not open")
            if self._index >= len(self._steps):
                raise ProviderError("scripted chat model has no remaining response")
            step = self._steps[self._index]
            self._index += 1
            if isinstance(step, ProviderError):
                raise step from None
            return step


class FixedClock:
    """Clock that always returns one timezone-aware UTC instant."""

    def __init__(self, value: datetime) -> None:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise DomainValidationError("fixed clock value must be timezone-aware")
        self._value = value.astimezone(UTC)

    def now(self) -> datetime:
        """Return the configured immutable UTC instant."""

        return self._value


class DeterministicIdGenerator:
    """Thread-safe per-purpose monotonic identifier generator."""

    def __init__(self, *, prefix: str = "memory-engine", start: int = 1) -> None:
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise DomainValidationError("identifier start must be a non-negative integer")
        self._prefix = _identifier(prefix, field_name="identifier prefix")
        self._next = {purpose: start for purpose in IdentifierPurpose}
        self._lock = threading.Lock()

    def new_id(self, *, purpose: IdentifierPurpose) -> str:
        """Return the next stable ID for one neutral purpose."""

        if not isinstance(purpose, IdentifierPurpose):
            raise DomainValidationError("identifier purpose must be IdentifierPurpose")
        with self._lock:
            value = self._next[purpose]
            self._next[purpose] = value + 1
        return f"{self._prefix}-{purpose.value}-{value:06d}"
