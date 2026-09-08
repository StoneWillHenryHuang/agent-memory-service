"""Execute the reusable store contract against a minimal behavioral model."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

import pytest

from portable_memory_engine.domain import (
    CapabilityError,
    ConditionalWrite,
    ConflictError,
    CountPrecision,
    DeleteCommand,
    DeleteMemoryCommand,
    DeleteResult,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    Embedding,
    IdempotencyConflictError,
    LifecycleError,
    MemoryKind,
    MemoryMatch,
    MemoryPage,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    PutOutcome,
    PutResult,
    PutStatus,
    SemanticQuery,
    SortOrder,
    StaleEventError,
)
from portable_memory_engine.ports import (
    HealthState,
    HealthStatus,
    MemoryStore,
    StorageCapabilities,
    StorageCapability,
)
from portable_memory_engine.testing.contracts import (
    MemoryStoreContractFactory,
    MemoryStoreContractSuite,
)
from portable_memory_engine.testing.contracts.memory_store import (
    assert_query_embedding,
    require_vector_search,
)

type _Identity = tuple[MemoryScope, str]
type _FreshnessIdentity = tuple[MemoryScope, str, MemoryKind]
type _ScopeBarrierIdentity = tuple[MemoryScope, MemoryKind | None]
type _CommittedResult = PutResult | DeleteResult


class _ContractModelStore:
    """Small test-only semantic model; Step 6 supplies the public adapter."""

    def __init__(self) -> None:
        self._open = False
        self._version = 0
        self._records: dict[_Identity, MemoryRecord] = {}
        self._idempotency: dict[tuple[MemoryScope, str], tuple[str, _CommittedResult]] = {}
        self._watermarks: dict[_FreshnessIdentity, datetime] = {}
        self._id_barriers: dict[_Identity, datetime] = {}
        self._scope_barriers: dict[_ScopeBarrierIdentity, datetime] = {}

    @property
    def capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(
            vector_search=True,
            atomic_compare_and_swap=True,
            atomic_idempotency=True,
            freshness_watermarks=True,
            deletion_barriers=True,
            metadata_filtering=False,
            transactions=False,
            exact_total_count=True,
        )

    async def open(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False

    async def health(self) -> HealthStatus:
        self._ensure_open()
        return HealthStatus(HealthState.HEALTHY)

    def _ensure_open(self) -> None:
        if not self._open:
            raise LifecycleError("memory store is not open")

    def _replay(self, *, scope: MemoryScope, key: str, digest: str) -> _CommittedResult | None:
        committed = self._idempotency.get((scope, key))
        if committed is None:
            return None
        committed_digest, result = committed
        if committed_digest != digest:
            raise IdempotencyConflictError(idempotency_key=key)
        if isinstance(result, PutResult):
            return PutResult(tuple(replace(outcome, replayed=True) for outcome in result.outcomes))
        return replace(result, replayed=True)

    def _effective_watermark(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
    ) -> datetime | None:
        candidates = (
            self._watermarks.get((scope, memory_id, kind)),
            self._id_barriers.get((scope, memory_id)),
            self._scope_barriers.get((scope, kind)),
            self._scope_barriers.get((scope, None)),
        )
        present = tuple(candidate for candidate in candidates if candidate is not None)
        return max(present) if present else None

    async def get(self, *, scope: MemoryScope, memory_id: str) -> MemoryRecord | None:
        self._ensure_open()
        return self._records.get((scope, memory_id))

    def _filtered(self, query: MemoryQuery | SemanticQuery) -> list[MemoryRecord]:
        records = [record for (scope, _), record in self._records.items() if scope == query.scope]
        if query.kinds:
            records = [record for record in records if record.kind in query.kinds]
        if query.source is not None:
            records = [record for record in records if record.provenance.source == query.source]
        if query.session_id is not None:
            records = [
                record for record in records if record.provenance.session_id == query.session_id
            ]
        return records

    async def query(self, query: MemoryQuery) -> MemoryPage:
        self._ensure_open()
        records = self._filtered(query)
        records.sort(
            key=lambda record: (record.updated_at, record.id),
            reverse=query.order is SortOrder.NEWEST,
        )
        selected = records[query.offset : query.offset + query.limit]
        next_offset = (
            query.offset + len(selected) if query.offset + len(selected) < len(records) else None
        )
        return MemoryPage(
            scope=query.scope,
            items=tuple(MemoryMatch(record) for record in selected),
            offset=query.offset,
            limit=query.limit,
            next_offset=next_offset,
            total_count=len(records),
            count_precision=CountPrecision.EXACT,
        )

    @staticmethod
    def _score(document: Embedding, query: Embedding) -> float:
        if document.model_id != query.model_id or document.dimension != query.dimension:
            raise CapabilityError(
                operation="semantic_search",
                capability="compatible_embedding_dimension",
            )
        numerator = sum(
            left * right for left, right in zip(document.values, query.values, strict=True)
        )
        document_norm = math.sqrt(sum(value * value for value in document.values))
        query_norm = math.sqrt(sum(value * value for value in query.values))
        if document_norm == 0 or query_norm == 0:
            return 0.0
        return (numerator / (document_norm * query_norm) + 1.0) / 2.0

    async def semantic_search(
        self,
        query: SemanticQuery,
        *,
        query_embedding: Embedding,
    ) -> MemoryPage:
        self._ensure_open()
        require_vector_search(self)
        assert_query_embedding(query_embedding)
        matches = [
            MemoryMatch(record, self._score(record.embedding, query_embedding))
            for record in self._filtered(query)
            if record.embedding is not None
        ]
        matches.sort(key=lambda match: match.score or 0.0, reverse=True)
        if query.min_score is not None:
            matches = [match for match in matches if (match.score or 0.0) >= query.min_score]
        selected = matches[query.offset : query.offset + query.limit]
        next_offset = (
            query.offset + len(selected) if query.offset + len(selected) < len(matches) else None
        )
        return MemoryPage(
            scope=query.scope,
            items=tuple(selected),
            offset=query.offset,
            limit=query.limit,
            next_offset=next_offset,
            total_count=len(matches),
            count_precision=CountPrecision.EXACT,
        )

    async def freshness_watermark(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
    ) -> datetime | None:
        self._ensure_open()
        return self._effective_watermark(scope=scope, memory_id=memory_id, kind=kind)

    async def put(self, command: ConditionalWrite) -> PutResult:
        self._ensure_open()
        replay = self._replay(
            scope=command.record.scope,
            key=command.idempotency_key,
            digest=command.payload_digest,
        )
        if replay is not None:
            if not isinstance(replay, PutResult):
                raise IdempotencyConflictError(idempotency_key=command.idempotency_key)
            return replay

        record = command.record
        watermark = self._effective_watermark(
            scope=record.scope,
            memory_id=record.id,
            kind=record.kind,
        )
        if watermark is not None and command.event_timestamp <= watermark:
            raise StaleEventError(event_timestamp=command.event_timestamp, watermark=watermark)

        identity = (record.scope, record.id)
        current = self._records.get(identity)
        if command.expected_version is None:
            if current is not None:
                raise ConflictError(memory_id=record.id)
            status = PutStatus.CREATED
        else:
            if current is None or current.version != command.expected_version:
                raise ConflictError(memory_id=record.id)
            status = PutStatus.UPDATED

        self._version += 1
        stored = replace(record, version=self._version)
        self._records[identity] = stored
        self._watermarks[(record.scope, record.id, record.kind)] = command.event_timestamp
        result = PutResult((PutOutcome(record.id, record.scope, status, self._version),))
        self._idempotency[(record.scope, command.idempotency_key)] = (
            command.payload_digest,
            result,
        )
        return result

    async def put_many(
        self,
        commands: Sequence[ConditionalWrite],
        *,
        atomic: bool = False,
    ) -> PutResult:
        self._ensure_open()
        if atomic:
            self.capabilities.require(
                operation="atomic_put_many",
                capabilities=(StorageCapability.TRANSACTIONS,),
            )
        outcomes: list[PutOutcome] = []
        for command in commands:
            outcomes.extend((await self.put(command)).outcomes)
        return PutResult(tuple(outcomes))

    async def delete(self, command: DeleteCommand) -> DeleteResult:
        self._ensure_open()
        if isinstance(command, DeleteSubjectCommand):
            raise CapabilityError(
                operation="delete_subject",
                capability=StorageCapability.SUBJECT_DELETION.value,
            )
        if isinstance(command, DeleteSessionCommand):
            raise CapabilityError(
                operation="delete_session",
                capability=StorageCapability.SESSION_CONTRIBUTIONS.value,
            )
        replay = self._replay(
            scope=command.scope,
            key=command.idempotency_key,
            digest=command.payload_digest,
        )
        if replay is not None:
            if not isinstance(replay, DeleteResult):
                raise IdempotencyConflictError(idempotency_key=command.idempotency_key)
            return replay

        identities: tuple[_Identity, ...]
        if isinstance(command, DeleteMemoryCommand):
            identities = ((command.scope, command.memory_id),)
            self._id_barriers[(command.scope, command.memory_id)] = command.event_timestamp
        elif isinstance(command, DeleteScopeCommand):
            identities = tuple(
                identity
                for identity, record in self._records.items()
                if identity[0] == command.scope
                and (not command.kinds or record.kind in command.kinds)
            )
            if command.kinds:
                for kind in command.kinds:
                    self._scope_barriers[(command.scope, kind)] = command.event_timestamp
            else:
                self._scope_barriers[(command.scope, None)] = command.event_timestamp

        existing = tuple(identity for identity in identities if identity in self._records)
        for identity in existing:
            del self._records[identity]
        absent_count = 1 if isinstance(command, DeleteMemoryCommand) and not existing else 0
        result = DeleteResult(
            matched_count=len(existing),
            hard_deleted_count=len(existing),
            contribution_deleted_count=0,
            tombstoned_count=0,
            already_absent_count=absent_count,
            barrier_timestamp=command.event_timestamp,
        )
        self._idempotency[(command.scope, command.idempotency_key)] = (
            command.payload_digest,
            result,
        )
        return result


@pytest.fixture
def memory_store_factory() -> MemoryStoreContractFactory:
    """Inject a fresh unopened behavioral model into every inherited test."""

    return _ContractModelStore


class TestContractModelStore(MemoryStoreContractSuite):
    """Prove the public contract suite is executable through factory injection."""


def test_behavioral_model_satisfies_the_runtime_protocol() -> None:
    assert isinstance(_ContractModelStore(), MemoryStore)
