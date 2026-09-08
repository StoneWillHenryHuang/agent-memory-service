"""Provider-neutral memory persistence port and capability declaration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from portable_memory_engine.domain import (
    ConditionalWrite,
    DeleteCommand,
    DeleteResult,
    Embedding,
    MemoryKind,
    MemoryPage,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    PutResult,
    SemanticQuery,
)
from portable_memory_engine.domain.errors import CapabilityError, DomainValidationError
from portable_memory_engine.ports.lifecycle import AsyncLifecycle

STORAGE_CONTRACT_VERSION = "1.1"


class StorageCapability(StrEnum):
    """Neutral storage behaviors that callers may require before work begins."""

    VECTOR_SEARCH = "vector_search"
    ATOMIC_COMPARE_AND_SWAP = "atomic_compare_and_swap"
    ATOMIC_IDEMPOTENCY = "atomic_idempotency"
    FRESHNESS_WATERMARKS = "freshness_watermarks"
    DELETION_BARRIERS = "deletion_barriers"
    METADATA_FILTERING = "metadata_filtering"
    TRANSACTIONS = "transactions"
    EXACT_TOTAL_COUNT = "exact_total_count"
    SUBJECT_DELETION = "subject_deletion"
    SESSION_CONTRIBUTIONS = "session_contributions"


class HealthState(StrEnum):
    """Current adapter availability, kept separate from semantic capabilities."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Sanitized adapter health without native error text or configuration."""

    state: HealthState
    detail_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, HealthState):
            raise DomainValidationError("health state must be HealthState")
        if self.detail_code is not None and (
            not isinstance(self.detail_code, str)
            or not self.detail_code
            or self.detail_code != self.detail_code.strip()
            or any(character.isspace() for character in self.detail_code)
        ):
            raise DomainValidationError("health detail_code must be an identifier")


MANDATORY_WRITE_CAPABILITIES = (
    StorageCapability.ATOMIC_COMPARE_AND_SWAP,
    StorageCapability.ATOMIC_IDEMPOTENCY,
    StorageCapability.FRESHNESS_WATERMARKS,
    StorageCapability.DELETION_BARRIERS,
)


@dataclass(frozen=True, slots=True)
class StorageCapabilities:
    """Immutable semantic capabilities reported by one configured store."""

    contract_version: str = STORAGE_CONTRACT_VERSION
    vector_search: bool = False
    atomic_compare_and_swap: bool = False
    atomic_idempotency: bool = False
    freshness_watermarks: bool = False
    deletion_barriers: bool = False
    metadata_filtering: bool = False
    transactions: bool = False
    exact_total_count: bool = False
    subject_deletion: bool = False
    session_contributions: bool = False

    def __post_init__(self) -> None:
        if self.contract_version != STORAGE_CONTRACT_VERSION:
            raise DomainValidationError("unsupported storage contract version")
        flags = (
            self.vector_search,
            self.atomic_compare_and_swap,
            self.atomic_idempotency,
            self.freshness_watermarks,
            self.deletion_barriers,
            self.metadata_filtering,
            self.transactions,
            self.exact_total_count,
            self.subject_deletion,
            self.session_contributions,
        )
        if any(not isinstance(flag, bool) for flag in flags):
            raise DomainValidationError("storage capability flags must be boolean")

    def supports(self, capability: StorageCapability) -> bool:
        """Return whether one neutral behavior is available end to end."""

        if not isinstance(capability, StorageCapability):
            raise DomainValidationError("capability must be StorageCapability")
        match capability:
            case StorageCapability.VECTOR_SEARCH:
                return self.vector_search
            case StorageCapability.ATOMIC_COMPARE_AND_SWAP:
                return self.atomic_compare_and_swap
            case StorageCapability.ATOMIC_IDEMPOTENCY:
                return self.atomic_idempotency
            case StorageCapability.FRESHNESS_WATERMARKS:
                return self.freshness_watermarks
            case StorageCapability.DELETION_BARRIERS:
                return self.deletion_barriers
            case StorageCapability.METADATA_FILTERING:
                return self.metadata_filtering
            case StorageCapability.TRANSACTIONS:
                return self.transactions
            case StorageCapability.EXACT_TOTAL_COUNT:
                return self.exact_total_count
            case StorageCapability.SUBJECT_DELETION:
                return self.subject_deletion
            case StorageCapability.SESSION_CONTRIBUTIONS:
                return self.session_contributions

    def require(
        self,
        *,
        operation: str,
        capabilities: Sequence[StorageCapability],
    ) -> None:
        """Raise for the first unsupported behavior instead of falling back."""

        for capability in capabilities:
            if not self.supports(capability):
                raise CapabilityError(operation=operation, capability=capability.value)


@runtime_checkable
class MemoryStore(AsyncLifecycle, Protocol):
    """Complete-scope storage boundary with atomic write safety contracts.

    Implementations translate native database or service failures into public,
    sanitized store exceptions. Native sessions, transactions, ORM objects,
    backend query primitives, query languages, and provider errors must not cross
    this boundary.
    """

    @property
    def capabilities(self) -> StorageCapabilities:
        """Return stable configured capabilities, not current health."""

        ...

    async def health(self) -> HealthStatus:
        """Return current sanitized availability without changing capabilities."""

        ...

    async def get(self, *, scope: MemoryScope, memory_id: str) -> MemoryRecord | None:
        """Return one record only within its complete scope."""

        ...

    async def query(self, query: MemoryQuery) -> MemoryPage:
        """Return a store-filtered recency page."""

        ...

    async def semantic_search(
        self,
        query: SemanticQuery,
        *,
        query_embedding: Embedding,
    ) -> MemoryPage:
        """Return descending semantic matches or raise ``CapabilityError``."""

        ...

    async def freshness_watermark(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
    ) -> datetime | None:
        """Return the effective aware-UTC watermark, or ``None``."""

        ...

    async def put(self, command: ConditionalWrite) -> PutResult:
        """Atomically apply idempotency, freshness, and expected-version checks."""

        ...

    async def put_many(
        self,
        commands: Sequence[ConditionalWrite],
        *,
        atomic: bool = False,
    ) -> PutResult:
        """Apply writes in order; atomic groups require ``transactions``."""

        ...

    async def delete(self, command: DeleteCommand) -> DeleteResult:
        """Apply one typed delete and atomically advance its bounded barriers."""

        ...
