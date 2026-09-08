"""Concurrent in-memory implementation of the public memory-store contract."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

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
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    IdempotencyConflictError,
    LifecycleError,
    MemoryKind,
    MemoryMatch,
    MemoryPage,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    MemorySubject,
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
    StorageCapabilities,
    StorageCapability,
)

type _Identity = tuple[MemoryScope, str]
type _FreshnessIdentity = tuple[MemoryScope, str, MemoryKind]
type _ScopeBarrierIdentity = tuple[MemoryScope, MemoryKind | None]
type _SubjectBarrierIdentity = tuple[MemorySubject, MemoryKind | None]
type _SessionBarrierIdentity = tuple[MemoryScope, str]
type _SessionContributionIdentity = tuple[MemoryScope, str, str]
type _CommandBoundary = MemoryScope | MemorySubject
type _CommittedResult = PutResult | DeleteResult


@dataclass(frozen=True, slots=True)
class _IdempotencyEntry:
    digest: str
    result: _CommittedResult


@dataclass(frozen=True, slots=True)
class _SessionContribution:
    """Adapter-private contribution state; no marker leaks into the domain."""

    kind: MemoryKind
    event_timestamp: datetime
    deleted_at: datetime | None = None


@dataclass(slots=True)
class _StoreState:
    records: dict[_Identity, MemoryRecord] = field(default_factory=dict)
    idempotency: dict[tuple[_CommandBoundary, str], _IdempotencyEntry] = field(default_factory=dict)
    watermarks: dict[_FreshnessIdentity, datetime] = field(default_factory=dict)
    id_barriers: dict[_Identity, datetime] = field(default_factory=dict)
    scope_barriers: dict[_ScopeBarrierIdentity, datetime] = field(default_factory=dict)
    subject_barriers: dict[_SubjectBarrierIdentity, datetime] = field(default_factory=dict)
    session_barriers: dict[_SessionBarrierIdentity, datetime] = field(default_factory=dict)
    contributions: dict[_SessionContributionIdentity, _SessionContribution] = field(
        default_factory=dict
    )
    version: int = 0

    def clone(self) -> _StoreState:
        """Return an isolated control-state copy for an atomic write group."""

        return _StoreState(
            records=self.records.copy(),
            idempotency=self.idempotency.copy(),
            watermarks=self.watermarks.copy(),
            id_barriers=self.id_barriers.copy(),
            scope_barriers=self.scope_barriers.copy(),
            subject_barriers=self.subject_barriers.copy(),
            session_barriers=self.session_barriers.copy(),
            contributions=self.contributions.copy(),
            version=self.version,
        )


class InMemoryMemoryStore:
    """Concurrency-safe, deterministic, process-local ``MemoryStore`` adapter.

    The adapter stores immutable domain values and bounded control metadata only.
    It is intended for tests, examples, and single-process development—not as a
    durable or multi-process store.
    """

    _CAPABILITIES = StorageCapabilities(
        vector_search=True,
        atomic_compare_and_swap=True,
        atomic_idempotency=True,
        freshness_watermarks=True,
        deletion_barriers=True,
        metadata_filtering=False,
        transactions=True,
        exact_total_count=True,
        subject_deletion=True,
        session_contributions=True,
    )

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = _StoreState()
        self._open = False

    @property
    def capabilities(self) -> StorageCapabilities:
        """Return the immutable implemented capability declaration."""

        return self._CAPABILITIES

    async def open(self) -> None:
        """Open the adapter; repeated calls preserve existing in-memory state."""

        async with self._lock:
            self._open = True

    async def close(self) -> None:
        """Close the adapter after in-flight operations; repeated calls are safe."""

        async with self._lock:
            self._open = False

    def _ensure_open(self) -> None:
        if not self._open:
            raise LifecycleError("memory store is not open")

    async def health(self) -> HealthStatus:
        """Report healthy while the adapter is open."""

        async with self._lock:
            self._ensure_open()
            return HealthStatus(HealthState.HEALTHY)

    @staticmethod
    def _replay(
        state: _StoreState,
        *,
        boundary: _CommandBoundary,
        key: str,
        digest: str,
    ) -> _CommittedResult | None:
        entry = state.idempotency.get((boundary, key))
        if entry is None:
            return None
        if entry.digest != digest:
            raise IdempotencyConflictError(idempotency_key=key)
        if isinstance(entry.result, PutResult):
            return PutResult(
                tuple(replace(outcome, replayed=True) for outcome in entry.result.outcomes)
            )
        return replace(entry.result, replayed=True)

    @staticmethod
    def _maximum(values: Sequence[datetime | None]) -> datetime | None:
        present = tuple(value for value in values if value is not None)
        return max(present) if present else None

    @classmethod
    def _effective_watermark(
        cls,
        state: _StoreState,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
        session_id: str | None = None,
    ) -> datetime | None:
        values = [
            state.watermarks.get((scope, memory_id, kind)),
            state.id_barriers.get((scope, memory_id)),
            state.scope_barriers.get((scope, kind)),
            state.scope_barriers.get((scope, None)),
            state.subject_barriers.get((scope.subject, kind)),
            state.subject_barriers.get((scope.subject, None)),
        ]
        if session_id is not None:
            values.append(state.session_barriers.get((scope, session_id)))
        return cls._maximum(values)

    @staticmethod
    def _is_visible(state: _StoreState, record: MemoryRecord) -> bool:
        if any(
            contribution.deleted_at is not None
            for (scope, _, memory_id), contribution in state.contributions.items()
            if scope == record.scope and memory_id == record.id
        ):
            return False
        session_id = record.provenance.session_id
        if session_id is None:
            return True
        barrier = state.session_barriers.get((record.scope, session_id))
        event_timestamp = record.provenance.event_timestamp or record.updated_at
        return barrier is None or event_timestamp > barrier

    async def get(self, *, scope: MemoryScope, memory_id: str) -> MemoryRecord | None:
        """Return one immutable record within its complete scope."""

        async with self._lock:
            self._ensure_open()
            record = self._state.records.get((scope, memory_id))
            return record if record is not None and self._is_visible(self._state, record) else None

    @staticmethod
    def _filtered(
        state: _StoreState,
        query: MemoryQuery | SemanticQuery,
    ) -> list[MemoryRecord]:
        records = [
            record
            for (scope, _), record in state.records.items()
            if scope == query.scope and InMemoryMemoryStore._is_visible(state, record)
        ]
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
        """Return a deterministic store-filtered recency page."""

        async with self._lock:
            self._ensure_open()
            records = self._filtered(self._state, query)
            records.sort(
                key=lambda record: (record.updated_at, record.id),
                reverse=query.order is SortOrder.NEWEST,
            )
            selected = records[query.offset : query.offset + query.limit]
            next_offset = (
                query.offset + len(selected)
                if query.offset + len(selected) < len(records)
                else None
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
    def _semantic_score(document: Embedding, query: Embedding) -> float:
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
        normalized = (numerator / (document_norm * query_norm) + 1.0) / 2.0
        return min(1.0, max(0.0, normalized))

    async def semantic_search(
        self,
        query: SemanticQuery,
        *,
        query_embedding: Embedding,
    ) -> MemoryPage:
        """Return deterministic descending cosine-similarity matches."""

        async with self._lock:
            self._ensure_open()
            self.capabilities.require(
                operation="semantic_search",
                capabilities=(StorageCapability.VECTOR_SEARCH,),
            )
            if query_embedding.task is not EmbeddingTask.QUERY:
                raise DomainValidationError("semantic search requires a query embedding")
            matches = [
                MemoryMatch(
                    record,
                    self._semantic_score(record.embedding, query_embedding),
                )
                for record in self._filtered(self._state, query)
                if record.embedding is not None
            ]
            if query.min_score is not None:
                matches = [
                    match
                    for match in matches
                    if match.score is not None and match.score >= query.min_score
                ]
            matches.sort(
                key=lambda match: (
                    match.score if match.score is not None else -1.0,
                    match.record.updated_at,
                    match.record.id,
                ),
                reverse=True,
            )
            selected = matches[query.offset : query.offset + query.limit]
            next_offset = (
                query.offset + len(selected)
                if query.offset + len(selected) < len(matches)
                else None
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
        """Return the effective record, ID, kind, or scope watermark."""

        async with self._lock:
            self._ensure_open()
            return self._effective_watermark(
                self._state,
                scope=scope,
                memory_id=memory_id,
                kind=kind,
            )

    @classmethod
    def _put(cls, state: _StoreState, command: ConditionalWrite) -> PutResult:
        replay = cls._replay(
            state,
            boundary=command.record.scope,
            key=command.idempotency_key,
            digest=command.payload_digest,
        )
        if replay is not None:
            if not isinstance(replay, PutResult):
                raise IdempotencyConflictError(idempotency_key=command.idempotency_key)
            return replay

        record = command.record
        identity = (record.scope, record.id)
        current = state.records.get(identity)
        if current is not None and current.kind != record.kind:
            raise DomainValidationError("a conditional write cannot change memory kind")
        watermark = cls._effective_watermark(
            state,
            scope=record.scope,
            memory_id=record.id,
            kind=record.kind,
            session_id=record.provenance.session_id,
        )
        if watermark is not None and command.event_timestamp <= watermark:
            raise StaleEventError(event_timestamp=command.event_timestamp, watermark=watermark)

        visible_current = (
            current if current is not None and cls._is_visible(state, current) else None
        )
        if command.expected_version is None:
            if visible_current is not None:
                raise ConflictError(memory_id=record.id)
            status = PutStatus.CREATED
        else:
            if visible_current is None or visible_current.version != command.expected_version:
                raise ConflictError(memory_id=record.id)
            status = PutStatus.UPDATED

        state.version += 1
        stored = replace(
            record,
            created_at=current.created_at if current is not None else record.created_at,
            version=state.version,
        )
        state.records[identity] = stored
        state.watermarks[(record.scope, record.id, record.kind)] = command.event_timestamp
        session_id = record.provenance.session_id
        if session_id is not None and record.kind in (MemoryKind.FACT, MemoryKind.SUMMARY):
            state.contributions[(record.scope, session_id, record.id)] = _SessionContribution(
                kind=record.kind,
                event_timestamp=command.event_timestamp,
            )
        result = PutResult((PutOutcome(record.id, record.scope, status, state.version),))
        state.idempotency[(record.scope, command.idempotency_key)] = _IdempotencyEntry(
            command.payload_digest,
            result,
        )
        return result

    async def put(self, command: ConditionalWrite) -> PutResult:
        """Atomically apply one idempotent freshness-guarded CAS write."""

        async with self._lock:
            self._ensure_open()
            return self._put(self._state, command)

    async def put_many(
        self,
        commands: Sequence[ConditionalWrite],
        *,
        atomic: bool = False,
    ) -> PutResult:
        """Apply a write group, committing all-or-none when ``atomic`` is true."""

        async with self._lock:
            self._ensure_open()
            if atomic:
                self.capabilities.require(
                    operation="atomic_put_many",
                    capabilities=(StorageCapability.TRANSACTIONS,),
                )
                candidate = self._state.clone()
                outcomes: list[PutOutcome] = []
                for command in commands:
                    outcomes.extend(self._put(candidate, command).outcomes)
                self._state = candidate
                return PutResult(tuple(outcomes))

            outcomes = []
            for command in commands:
                outcomes.extend(self._put(self._state, command).outcomes)
            return PutResult(tuple(outcomes))

    @staticmethod
    def _advance_barrier(current: datetime | None, proposed: datetime) -> datetime:
        return max(current, proposed) if current is not None else proposed

    @staticmethod
    def _remove_record(state: _StoreState, record: MemoryRecord) -> None:
        del state.records[(record.scope, record.id)]
        for identity in tuple(state.contributions):
            scope, _, memory_id = identity
            if scope == record.scope and memory_id == record.id:
                del state.contributions[identity]

    @classmethod
    def _validate_delete_freshness(
        cls,
        state: _StoreState,
        *,
        event_timestamp: datetime,
        records: Sequence[MemoryRecord],
        contribution_timestamps: Sequence[datetime] = (),
    ) -> None:
        watermarks = list(contribution_timestamps)
        watermarks.extend(
            watermark
            for record in records
            if (
                watermark := cls._effective_watermark(
                    state,
                    scope=record.scope,
                    memory_id=record.id,
                    kind=record.kind,
                    session_id=record.provenance.session_id,
                )
            )
            is not None
        )
        latest = max(watermarks) if watermarks else None
        if latest is not None and event_timestamp <= latest:
            raise StaleEventError(event_timestamp=event_timestamp, watermark=latest)

    @classmethod
    def _delete(cls, state: _StoreState, command: DeleteCommand) -> DeleteResult:
        boundary: _CommandBoundary = (
            command.subject if isinstance(command, DeleteSubjectCommand) else command.scope
        )
        replay = cls._replay(
            state,
            boundary=boundary,
            key=command.idempotency_key,
            digest=command.payload_digest,
        )
        if replay is not None:
            if not isinstance(replay, DeleteResult):
                raise IdempotencyConflictError(idempotency_key=command.idempotency_key)
            return replay

        records: tuple[MemoryRecord, ...] = ()
        contributions: tuple[tuple[_SessionContributionIdentity, _SessionContribution], ...] = ()
        already_absent_count = 0
        if isinstance(command, DeleteMemoryCommand):
            memory_identity = (command.scope, command.memory_id)
            record = state.records.get(memory_identity)
            records = (record,) if record is not None else ()
            already_absent_count = 0 if records else 1
        elif isinstance(command, DeleteScopeCommand):
            records = tuple(
                record
                for (scope, _), record in state.records.items()
                if scope == command.scope and (not command.kinds or record.kind in command.kinds)
            )
        elif isinstance(command, DeleteSubjectCommand):
            records = tuple(
                record
                for record in state.records.values()
                if record.scope.subject == command.subject
                and (not command.kinds or record.kind in command.kinds)
            )
        elif isinstance(command, DeleteSessionCommand):
            contributions = tuple(
                (contribution_identity, contribution)
                for contribution_identity, contribution in state.contributions.items()
                if contribution_identity[0] == command.scope
                and contribution_identity[1] == command.session_id
                and contribution.deleted_at is None
            )
            records = tuple(
                record
                for record in state.records.values()
                if record.scope == command.scope
                and record.provenance.session_id == command.session_id
                and record.kind not in (MemoryKind.FACT, MemoryKind.SUMMARY)
            )
        cls._validate_delete_freshness(
            state,
            event_timestamp=command.event_timestamp,
            records=records,
            contribution_timestamps=tuple(
                contribution.event_timestamp for _, contribution in contributions
            ),
        )
        for record in records:
            cls._remove_record(state, record)
        for contribution_identity, contribution in contributions:
            state.contributions[contribution_identity] = replace(
                contribution,
                deleted_at=command.event_timestamp,
            )

        barrier_timestamp = command.event_timestamp
        if isinstance(command, DeleteMemoryCommand):
            id_barrier_key = (command.scope, command.memory_id)
            barrier_timestamp = cls._advance_barrier(
                state.id_barriers.get(id_barrier_key), command.event_timestamp
            )
            state.id_barriers[id_barrier_key] = barrier_timestamp
        elif isinstance(command, DeleteScopeCommand) and command.kinds:
            for kind in command.kinds:
                scope_kind_key = (command.scope, kind)
                value = cls._advance_barrier(
                    state.scope_barriers.get(scope_kind_key), command.event_timestamp
                )
                state.scope_barriers[scope_kind_key] = value
                barrier_timestamp = max(barrier_timestamp, value)
        elif isinstance(command, DeleteScopeCommand):
            scope_barrier_key = (command.scope, None)
            barrier_timestamp = cls._advance_barrier(
                state.scope_barriers.get(scope_barrier_key), command.event_timestamp
            )
            state.scope_barriers[scope_barrier_key] = barrier_timestamp
        elif isinstance(command, DeleteSubjectCommand) and command.kinds:
            for kind in command.kinds:
                subject_kind_key = (command.subject, kind)
                value = cls._advance_barrier(
                    state.subject_barriers.get(subject_kind_key), command.event_timestamp
                )
                state.subject_barriers[subject_kind_key] = value
                barrier_timestamp = max(barrier_timestamp, value)
        elif isinstance(command, DeleteSubjectCommand):
            subject_barrier_key = (command.subject, None)
            barrier_timestamp = cls._advance_barrier(
                state.subject_barriers.get(subject_barrier_key), command.event_timestamp
            )
            state.subject_barriers[subject_barrier_key] = barrier_timestamp
        elif isinstance(command, DeleteSessionCommand):
            session_barrier_key = (command.scope, command.session_id)
            barrier_timestamp = cls._advance_barrier(
                state.session_barriers.get(session_barrier_key), command.event_timestamp
            )
            state.session_barriers[session_barrier_key] = barrier_timestamp

        result = DeleteResult(
            matched_count=len(records) + len(contributions),
            hard_deleted_count=len(records),
            contribution_deleted_count=len(contributions),
            tombstoned_count=len(contributions),
            already_absent_count=already_absent_count,
            barrier_timestamp=barrier_timestamp,
        )
        state.idempotency[(boundary, command.idempotency_key)] = _IdempotencyEntry(
            command.payload_digest,
            result,
        )
        return result

    async def delete(self, command: DeleteCommand) -> DeleteResult:
        """Atomically apply hard, subject-wide, or contribution deletion."""

        async with self._lock:
            self._ensure_open()
            if isinstance(command, DeleteSubjectCommand):
                self.capabilities.require(
                    operation="delete_subject",
                    capabilities=(StorageCapability.SUBJECT_DELETION,),
                )
            if isinstance(command, DeleteSessionCommand):
                self.capabilities.require(
                    operation="delete_session",
                    capabilities=(StorageCapability.SESSION_CONTRIBUTIONS,),
                )
            return self._delete(self._state, command)
