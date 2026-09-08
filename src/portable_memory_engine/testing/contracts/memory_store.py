"""Reusable pytest contract suite for complete v0.1 memory stores.

Adapter packages subclass ``MemoryStoreContractSuite`` and provide a function-
scoped ``memory_store_factory`` fixture returning a new, unopened store. The
suite owns each instance for the duration of a test and always closes it.
Importing this optional module requires pytest; importing the base package does
not.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from portable_memory_engine.domain import (
    CapabilityError,
    ConditionalWrite,
    CountPrecision,
    DeleteMemoryCommand,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    IdempotencyConflictError,
    LifecycleError,
    MemoryKind,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    MemorySubject,
    SemanticQuery,
    SortOrder,
    StaleEventError,
)
from portable_memory_engine.domain.errors import ConflictError
from portable_memory_engine.ports.storage import (
    MANDATORY_WRITE_CAPABILITIES,
    STORAGE_CONTRACT_VERSION,
    HealthState,
    MemoryStore,
    StorageCapability,
)

type MemoryStoreContractFactory = Callable[[], MemoryStore]

_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _scope(subject: str = "subject-1") -> MemoryScope:
    return MemoryScope(tenant_id="tenant-1", subject_id=subject, namespace="contract")


def _record(
    memory_id: str,
    *,
    scope: MemoryScope | None = None,
    kind: MemoryKind = MemoryKind.FACT,
    timestamp: datetime = _BASE_TIME,
    source: str = "contract",
    session_id: str | None = None,
    embedding: Embedding | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=scope or _scope(),
        kind=kind,
        content=f"synthetic-{memory_id}",
        created_at=timestamp,
        updated_at=timestamp,
        provenance=MemoryProvenance(
            source=source,
            session_id=session_id,
            event_timestamp=timestamp,
        ),
        embedding=embedding,
    )


def _write(
    record: MemoryRecord,
    *,
    key: str,
    timestamp: datetime | None = None,
    digest: str | None = None,
    expected_version: str | int | None = None,
) -> ConditionalWrite:
    return ConditionalWrite(
        record=record,
        idempotency_key=key,
        payload_digest=digest or f"digest:{key}",
        event_timestamp=timestamp or record.updated_at,
        expected_version=expected_version,
    )


class MemoryStoreContractSuite:
    """Inherited pytest tests for a factory-injected ``MemoryStore`` adapter."""

    def test_lifecycle_and_mandatory_capabilities(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify explicit lifecycle and the full v0.1 write safety profile."""

        asyncio.run(self._lifecycle_and_capabilities(memory_store_factory))

    async def _lifecycle_and_capabilities(
        self,
        factory: MemoryStoreContractFactory,
    ) -> None:
        store = factory()
        await store.close()
        await store.close()
        with pytest.raises(LifecycleError):
            await store.get(scope=_scope(), memory_id="missing")
        await store.open()
        await store.open()
        assert store.capabilities.contract_version == STORAGE_CONTRACT_VERSION
        assert (await store.health()).state is HealthState.HEALTHY
        store.capabilities.require(
            operation="contract_write",
            capabilities=MANDATORY_WRITE_CAPABILITIES,
        )
        await store.close()

    def test_complete_scope_isolation(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify IDs, reads, pages, and counts are isolated by complete scope."""

        asyncio.run(self._complete_scope_isolation(memory_store_factory))

    async def _complete_scope_isolation(self, factory: MemoryStoreContractFactory) -> None:
        store = factory()
        await store.open()
        try:
            first_scope = _scope("subject-a")
            second_scope = _scope("subject-b")
            await store.put(_write(_record("shared", scope=first_scope), key="scope-a"))
            await store.put(_write(_record("shared", scope=second_scope), key="scope-b"))

            first = await store.get(scope=first_scope, memory_id="shared")
            second = await store.get(scope=second_scope, memory_id="shared")
            assert first is not None and first.scope == first_scope
            assert second is not None and second.scope == second_scope
            assert first != second

            page = await store.query(MemoryQuery(scope=first_scope))
            assert page.scope == first_scope
            assert [item.record.scope for item in page.items] == [first_scope]
            assert [item.record.content for item in page.items] == ["synthetic-shared"]
        finally:
            await store.close()

    def test_idempotency_and_occ(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify replay, key conflicts, create-only, and version CAS behavior."""

        asyncio.run(self._idempotency_and_occ(memory_store_factory))

    async def _idempotency_and_occ(self, factory: MemoryStoreContractFactory) -> None:
        store = factory()
        await store.open()
        try:
            record = _record("occ")
            command = _write(record, key="occ-create")
            created = await store.put(command)
            replay = await store.put(command)
            assert len(created.outcomes) == len(replay.outcomes) == 1
            assert replay.outcomes[0].version == created.outcomes[0].version
            assert replay.outcomes[0].replayed is True

            with pytest.raises(IdempotencyConflictError):
                await store.put(replace(command, payload_digest="digest:different"))
            with pytest.raises(ConflictError):
                await store.put(_write(record, key="occ-duplicate-create"))
            with pytest.raises(ConflictError):
                await store.put(
                    _write(
                        replace(
                            record, content="updated", updated_at=_BASE_TIME + timedelta(seconds=1)
                        ),
                        key="occ-wrong-version",
                        timestamp=_BASE_TIME + timedelta(seconds=1),
                        expected_version="wrong",
                    )
                )

            updated = await store.put(
                _write(
                    replace(
                        record, content="updated", updated_at=_BASE_TIME + timedelta(seconds=1)
                    ),
                    key="occ-update",
                    timestamp=_BASE_TIME + timedelta(seconds=1),
                    expected_version=created.outcomes[0].version,
                )
            )
            assert updated.outcomes[0].version != created.outcomes[0].version
        finally:
            await store.close()

    def test_event_time_freshness(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify stale and same-time competing events cannot overwrite state."""

        asyncio.run(self._event_time_freshness(memory_store_factory))

    async def _event_time_freshness(self, factory: MemoryStoreContractFactory) -> None:
        store = factory()
        await store.open()
        try:
            record = _record("fresh")
            created = await store.put(_write(record, key="fresh-create"))
            with pytest.raises(StaleEventError):
                await store.put(
                    _write(
                        replace(
                            record,
                            created_at=_BASE_TIME - timedelta(seconds=1),
                            updated_at=_BASE_TIME - timedelta(seconds=1),
                        ),
                        key="fresh-old",
                        timestamp=_BASE_TIME - timedelta(seconds=1),
                        expected_version=created.outcomes[0].version,
                    )
                )
            with pytest.raises(StaleEventError):
                await store.put(
                    _write(
                        record,
                        key="fresh-equal-competitor",
                        expected_version=created.outcomes[0].version,
                    )
                )

            later = _BASE_TIME + timedelta(seconds=2)
            await store.put(
                _write(
                    replace(record, updated_at=later),
                    key="fresh-new",
                    timestamp=later,
                    expected_version=created.outcomes[0].version,
                )
            )
            watermark = await store.freshness_watermark(
                scope=record.scope,
                memory_id=record.id,
                kind=record.kind,
            )
            assert watermark == later
        finally:
            await store.close()

    def test_pagination_filtering_and_ordering(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify stable ordering, filters, offsets, limits, and count precision."""

        asyncio.run(self._pagination_filtering_and_ordering(memory_store_factory))

    async def _pagination_filtering_and_ordering(
        self,
        factory: MemoryStoreContractFactory,
    ) -> None:
        store = factory()
        await store.open()
        try:
            scope = _scope()
            for index in range(4):
                timestamp = _BASE_TIME + timedelta(seconds=index)
                await store.put(
                    _write(
                        _record(
                            f"page-{index}",
                            scope=scope,
                            timestamp=timestamp,
                            source="selected" if index != 0 else "other",
                            session_id="session-1",
                        ),
                        key=f"page-{index}",
                    )
                )

            page = await store.query(
                MemoryQuery(
                    scope=scope,
                    source="selected",
                    session_id="session-1",
                    offset=1,
                    limit=2,
                    order=SortOrder.NEWEST,
                )
            )
            assert [match.record.id for match in page.items] == ["page-2", "page-1"]
            if store.capabilities.exact_total_count:
                assert page.count_precision is CountPrecision.EXACT
                assert page.total_count == 3
            else:
                assert page.count_precision is not CountPrecision.EXACT
        finally:
            await store.close()

    def test_id_and_scope_deletion_barriers(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify content-free ID/kind barriers and idempotent delete replay."""

        asyncio.run(self._id_and_scope_deletion_barriers(memory_store_factory))

    async def _id_and_scope_deletion_barriers(
        self,
        factory: MemoryStoreContractFactory,
    ) -> None:
        store = factory()
        await store.open()
        try:
            scope = _scope()
            fact = _record("delete-fact", scope=scope)
            profile = _record("delete-profile", scope=scope, kind=MemoryKind.PROFILE)
            await store.put(_write(fact, key="delete-fact-create"))
            await store.put(_write(profile, key="delete-profile-create"))

            deletion_time = _BASE_TIME + timedelta(seconds=1)
            delete_id = DeleteMemoryCommand(
                scope=scope,
                memory_id=fact.id,
                idempotency_key="delete-id",
                payload_digest="digest:delete-id",
                event_timestamp=deletion_time,
            )
            deleted = await store.delete(delete_id)
            replay = await store.delete(delete_id)
            assert deleted.deleted_count == 1
            assert replay.replayed is True
            assert await store.get(scope=scope, memory_id=fact.id) is None
            with pytest.raises(IdempotencyConflictError):
                await store.delete(replace(delete_id, payload_digest="digest:different"))
            with pytest.raises(StaleEventError):
                await store.put(
                    _write(
                        replace(fact, updated_at=deletion_time),
                        key="delete-id-stale-recreate",
                        timestamp=deletion_time,
                    )
                )

            scope_delete_time = deletion_time + timedelta(seconds=1)
            scope_delete = DeleteScopeCommand(
                scope=scope,
                kinds=frozenset({MemoryKind.PROFILE}),
                idempotency_key="delete-scope",
                payload_digest="digest:delete-scope",
                event_timestamp=scope_delete_time,
            )
            scope_result = await store.delete(scope_delete)
            assert scope_result.deleted_count == 1
            with pytest.raises(StaleEventError):
                await store.put(
                    _write(
                        _record(
                            "new-profile",
                            scope=scope,
                            kind=MemoryKind.PROFILE,
                            timestamp=scope_delete_time,
                        ),
                        key="delete-scope-stale-recreate",
                    )
                )
            newer = scope_delete_time + timedelta(seconds=1)
            await store.put(
                _write(
                    _record(
                        "new-profile",
                        scope=scope,
                        kind=MemoryKind.PROFILE,
                        timestamp=newer,
                    ),
                    key="delete-scope-new-recreate",
                )
            )
        finally:
            await store.close()

    def test_subject_deletion_or_explicit_capability_error(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify subject-wide isolation and barriers, or an explicit refusal."""

        asyncio.run(self._subject_deletion(memory_store_factory))

    async def _subject_deletion(self, factory: MemoryStoreContractFactory) -> None:
        store = factory()
        await store.open()
        try:
            subject = MemorySubject(subject_id="subject-wide", tenant_id="tenant-a")
            first_scope = MemoryScope(
                subject_id=subject.subject_id,
                namespace="namespace-a",
                tenant_id=subject.tenant_id,
            )
            second_scope = MemoryScope(
                subject_id=subject.subject_id,
                namespace="namespace-b",
                tenant_id=subject.tenant_id,
            )
            other_tenant = MemoryScope(
                subject_id=subject.subject_id,
                namespace="namespace-a",
                tenant_id="tenant-b",
            )
            first = _record("subject-fact-a", scope=first_scope)
            second = _record("subject-fact-b", scope=second_scope)
            preserved = _record("other-tenant-fact", scope=other_tenant)
            for index, record in enumerate((first, second, preserved)):
                await store.put(_write(record, key=f"subject-create-{index}"))

            command = DeleteSubjectCommand(
                subject=subject,
                kinds=frozenset({MemoryKind.FACT}),
                idempotency_key="delete-subject",
                payload_digest="digest:delete-subject",
                event_timestamp=_BASE_TIME + timedelta(seconds=1),
            )
            if not store.capabilities.subject_deletion:
                with pytest.raises(CapabilityError):
                    await store.delete(command)
                assert await store.get(scope=first_scope, memory_id=first.id) is not None
                return

            result = await store.delete(command)
            assert result.hard_deleted_count == 2
            assert await store.get(scope=first_scope, memory_id=first.id) is None
            assert await store.get(scope=second_scope, memory_id=second.id) is None
            assert await store.get(scope=other_tenant, memory_id=preserved.id) is not None
            repeated = await store.delete(replace(command, idempotency_key="delete-subject-again"))
            assert repeated.deleted_count == 0
            with pytest.raises(StaleEventError):
                await store.put(
                    _write(
                        _record(
                            "subject-late",
                            scope=second_scope,
                            timestamp=command.event_timestamp,
                        ),
                        key="subject-late",
                    )
                )
        finally:
            await store.close()

    def test_session_contribution_deletion_or_explicit_capability_error(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify per-kind session behavior and complete-scope isolation."""

        asyncio.run(self._session_contribution_deletion(memory_store_factory))

    async def _session_contribution_deletion(
        self,
        factory: MemoryStoreContractFactory,
    ) -> None:
        store = factory()
        await store.open()
        try:
            scope = _scope()
            other_scope = _scope(subject="other-session-subject")
            session_id = "session-delete"
            fact = _record("session-fact", scope=scope, session_id=session_id)
            summary = _record(
                "session-summary",
                scope=scope,
                kind=MemoryKind.SUMMARY,
                session_id=session_id,
            )
            profile = _record(
                "session-profile",
                scope=scope,
                kind=MemoryKind.PROFILE,
                session_id=session_id,
            )
            preserved = _record(
                "other-scope-session-fact",
                scope=other_scope,
                session_id=session_id,
            )
            for index, record in enumerate((fact, summary, profile, preserved)):
                await store.put(_write(record, key=f"session-create-{index}"))

            command = DeleteSessionCommand(
                scope=scope,
                session_id=session_id,
                idempotency_key="delete-session",
                payload_digest="digest:delete-session",
                event_timestamp=_BASE_TIME + timedelta(seconds=1),
            )
            if not store.capabilities.session_contributions:
                with pytest.raises(CapabilityError):
                    await store.delete(command)
                assert await store.get(scope=scope, memory_id=fact.id) is not None
                return

            result = await store.delete(command)
            assert result.hard_deleted_count == 1
            assert result.contribution_deleted_count == 2
            assert result.tombstoned_count == 2
            assert await store.get(scope=scope, memory_id=fact.id) is None
            assert await store.get(scope=scope, memory_id=summary.id) is None
            assert await store.get(scope=scope, memory_id=profile.id) is None
            assert await store.get(scope=other_scope, memory_id=preserved.id) is not None

            replay = await store.delete(command)
            assert replay.replayed is True
            assert replay.deleted_count == 3
            repeated = await store.delete(
                replace(
                    command,
                    idempotency_key="delete-session-again",
                    event_timestamp=command.event_timestamp + timedelta(seconds=1),
                )
            )
            assert repeated.deleted_count == 0

            with pytest.raises(StaleEventError):
                await store.put(
                    _write(
                        _record(
                            "session-late",
                            scope=scope,
                            session_id=session_id,
                            timestamp=repeated.barrier_timestamp,
                        ),
                        key="session-late",
                    )
                )
            regenerated_at = repeated.barrier_timestamp + timedelta(seconds=1)
            await store.put(
                _write(
                    replace(
                        fact,
                        updated_at=regenerated_at,
                        provenance=replace(
                            fact.provenance,
                            event_timestamp=regenerated_at,
                        ),
                    ),
                    key="session-regenerated",
                    timestamp=regenerated_at,
                )
            )
            assert await store.get(scope=scope, memory_id=fact.id) is not None
        finally:
            await store.close()

    def test_semantic_ordering_or_explicit_capability_error(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify descending scores or an explicit no-vector capability failure."""

        asyncio.run(self._semantic_ordering(memory_store_factory))

    async def _semantic_ordering(self, factory: MemoryStoreContractFactory) -> None:
        store = factory()
        await store.open()
        try:
            query_embedding = Embedding(
                values=(1.0, 0.0),
                model_id="contract-embedding",
                task=EmbeddingTask.QUERY,
            )
            query = SemanticQuery(scope=_scope(), text="synthetic query", limit=10)
            if not store.capabilities.vector_search:
                with pytest.raises(CapabilityError):
                    await store.semantic_search(query, query_embedding=query_embedding)
                return

            await store.put(
                _write(
                    _record(
                        "semantic-best",
                        embedding=Embedding(
                            values=(1.0, 0.0),
                            model_id="contract-embedding",
                            task=EmbeddingTask.DOCUMENT,
                        ),
                    ),
                    key="semantic-best",
                )
            )
            await store.put(
                _write(
                    _record(
                        "semantic-second",
                        embedding=Embedding(
                            values=(0.5, 0.5),
                            model_id="contract-embedding",
                            task=EmbeddingTask.DOCUMENT,
                        ),
                    ),
                    key="semantic-second",
                )
            )
            page = await store.semantic_search(query, query_embedding=query_embedding)
            assert [match.record.id for match in page.items] == [
                "semantic-best",
                "semantic-second",
            ]
            scores = [match.score for match in page.items]
            assert all(score is not None for score in scores)
            assert scores == sorted(
                (score for score in scores if score is not None),
                reverse=True,
            )
        finally:
            await store.close()

    def test_atomic_groups_are_real_or_rejected(
        self,
        memory_store_factory: MemoryStoreContractFactory,
    ) -> None:
        """Verify atomic grouping is capability-backed and never emulated silently."""

        asyncio.run(self._atomic_groups(memory_store_factory))

    async def _atomic_groups(self, factory: MemoryStoreContractFactory) -> None:
        store = factory()
        await store.open()
        try:
            commands = (
                _write(_record("atomic-a"), key="atomic-a"),
                _write(_record("atomic-b"), key="atomic-b"),
            )
            if store.capabilities.transactions:
                result = await store.put_many(commands, atomic=True)
                assert len(result.outcomes) == 2
            else:
                with pytest.raises(CapabilityError):
                    await store.put_many(commands, atomic=True)
                assert await store.get(scope=_scope(), memory_id="atomic-a") is None
                assert await store.get(scope=_scope(), memory_id="atomic-b") is None
        finally:
            await store.close()


def assert_query_embedding(embedding: Embedding) -> None:
    """Raise before vector work when an embedding has the wrong task."""

    if embedding.task is not EmbeddingTask.QUERY:
        raise DomainValidationError("semantic search requires a query embedding")


def require_vector_search(store: MemoryStore, *, operation: str = "semantic_search") -> None:
    """Raise before vector work when the capability is unavailable."""

    store.capabilities.require(
        operation=operation,
        capabilities=(StorageCapability.VECTOR_SEARCH,),
    )
