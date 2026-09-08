"""Transactional PostgreSQL and pgvector implementation of ``MemoryStore``."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Never, Protocol, cast

from pgvector.psycopg import register_vector_async
from sqlalchemy import (
    ColumnElement,
    Table,
    and_,
    event,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import aliased

from portable_memory_engine.adapters.postgres.config import PostgresStoreConfig
from portable_memory_engine.adapters.postgres.mapper import (
    delete_result_from_json,
    delete_result_to_json,
    memory_values,
    put_result_from_json,
    put_result_to_json,
    row_to_memory,
    tenant_to_storage,
)
from portable_memory_engine.adapters.postgres.migrations import upgrade_postgres_schema
from portable_memory_engine.adapters.postgres.models import (
    BarrierRow,
    ContributionRow,
    FreshnessRow,
    IdempotencyRow,
    MemoryRow,
    StoreConfigRow,
)
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
    MemoryEngineError,
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
    StoreUnavailableError,
)
from portable_memory_engine.ports import HealthState, HealthStatus, StorageCapabilities

logger = logging.getLogger(__name__)

_EMBEDDING_DIMENSION_KEY = "embedding_dimension"
_ALL_KINDS = ""
_NO_TARGET = ""
_SUBJECT_NAMESPACE = ""


class _AsyncDriverConnection(Protocol):
    def run_async(self, function: Callable[[object], Awaitable[None]]) -> None: ...


def _register_pgvector(dbapi_connection: object, _connection_record: object) -> None:
    connection = cast("_AsyncDriverConnection", dbapi_connection)
    register = cast("Callable[[object], Awaitable[None]]", register_vector_async)
    connection.run_async(register)


def _subject_lock_key(subject: MemorySubject) -> int:
    material = f"{tenant_to_storage(subject.tenant_id)}\0{subject.subject_id}".encode()
    digest = hashlib.blake2b(material, digest_size=8, person=b"pme-lock").digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def _scope_conditions(scope: MemoryScope) -> tuple[ColumnElement[bool], ...]:
    return (
        MemoryRow.tenant_id == tenant_to_storage(scope.tenant_id),
        MemoryRow.subject_id == scope.subject_id,
        MemoryRow.namespace == scope.namespace,
    )


def _identity_conditions(scope: MemoryScope, memory_id: str) -> tuple[ColumnElement[bool], ...]:
    return (*_scope_conditions(scope), MemoryRow.memory_id == memory_id)


class PostgresMemoryStore:
    """Durable complete-scope store backed by ordinary PostgreSQL and pgvector.

    The public default is exact cosine search. It is deterministic, has no
    dataset-specific tuning knobs, and works immediately after the base migration.
    Approximate indexes remain an explicit deployment decision.
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

    def __init__(self, config: PostgresStoreConfig) -> None:
        if not isinstance(config, PostgresStoreConfig):
            raise DomainValidationError("config must be PostgresStoreConfig")
        self._config = config
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def capabilities(self) -> StorageCapabilities:
        return self._CAPABILITIES

    async def open(self) -> None:
        """Open the pool and validate migration state and embedding dimension."""

        async with self._lifecycle_lock:
            if self._engine is not None:
                return
            if self._config.auto_migrate:
                try:
                    await upgrade_postgres_schema(self._config)
                except SQLAlchemyError as error:
                    self._raise_unavailable("migrate", error)
            engine = create_async_engine(
                self._config.sqlalchemy_url,
                pool_size=self._config.pool_size,
                max_overflow=self._config.max_overflow,
                pool_timeout=float(self._config.pool_timeout_seconds),
                pool_pre_ping=True,
                hide_parameters=True,
            )
            event.listen(engine.sync_engine, "connect", _register_pgvector)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions.begin() as session:
                    await session.execute(
                        postgres_insert(StoreConfigRow)
                        .values(
                            config_key=_EMBEDDING_DIMENSION_KEY,
                            integer_value=self._config.embedding_dimension,
                        )
                        .on_conflict_do_nothing(index_elements=[StoreConfigRow.config_key])
                    )
                    stored_dimension = await session.scalar(
                        select(StoreConfigRow.integer_value).where(
                            StoreConfigRow.config_key == _EMBEDDING_DIMENSION_KEY
                        )
                    )
                    if stored_dimension != self._config.embedding_dimension:
                        raise DomainValidationError(
                            "configured embedding_dimension does not match the database"
                        )
            except MemoryEngineError:
                await engine.dispose()
                raise
            except SQLAlchemyError as error:
                await engine.dispose()
                self._raise_unavailable("open", error)
            self._engine = engine
            self._session_factory = sessions
            logger.info("PostgreSQL memory store opened")

    async def close(self) -> None:
        """Dispose the pool; repeated closes are safe."""

        async with self._lifecycle_lock:
            engine = self._engine
            self._engine = None
            self._session_factory = None
        if engine is not None:
            await engine.dispose()

    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise LifecycleError("memory store is not open")
        return self._session_factory

    @staticmethod
    def _raise_unavailable(operation: str, _error: BaseException) -> Never:
        logger.warning("PostgreSQL memory store operation failed", extra={"operation": operation})
        raise StoreUnavailableError("memory store is unavailable") from None

    async def health(self) -> HealthStatus:
        """Return sanitized health without exposing a URL or native error."""

        sessions = self._sessions()
        try:
            async with sessions() as session:
                await session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return HealthStatus(HealthState.UNHEALTHY, "database_unavailable")
        return HealthStatus(HealthState.HEALTHY)

    @staticmethod
    async def _lock_subject(session: AsyncSession, subject: MemorySubject) -> None:
        await session.execute(select(func.pg_advisory_xact_lock(_subject_lock_key(subject))))

    @staticmethod
    def _visibility_condition() -> ColumnElement[bool]:
        contribution = aliased(ContributionRow)
        barrier = aliased(BarrierRow)
        deleted_contribution = exists(
            select(literal(1)).where(
                contribution.tenant_id == MemoryRow.tenant_id,
                contribution.subject_id == MemoryRow.subject_id,
                contribution.namespace == MemoryRow.namespace,
                contribution.memory_id == MemoryRow.memory_id,
                contribution.deleted_at.is_not(None),
            )
        )
        terminated_session = exists(
            select(literal(1)).where(
                barrier.boundary_type == "session",
                barrier.tenant_id == MemoryRow.tenant_id,
                barrier.subject_id == MemoryRow.subject_id,
                barrier.namespace == MemoryRow.namespace,
                barrier.target_key == MemoryRow.session_id,
                barrier.kind == _ALL_KINDS,
                barrier.barrier_timestamp
                >= func.coalesce(
                    MemoryRow.provenance_event_timestamp,
                    MemoryRow.updated_at,
                ),
            )
        )
        return and_(
            ~deleted_contribution,
            or_(MemoryRow.session_id.is_(None), ~terminated_session),
        )

    @classmethod
    async def _row_is_visible(cls, session: AsyncSession, row: MemoryRow) -> bool:
        result = await session.scalar(
            select(literal(True)).where(
                *_identity_conditions(
                    MemoryScope(
                        tenant_id=row.tenant_id or None,
                        subject_id=row.subject_id,
                        namespace=row.namespace,
                    ),
                    row.memory_id,
                ),
                cls._visibility_condition(),
            )
        )
        return result is True

    async def get(self, *, scope: MemoryScope, memory_id: str) -> MemoryRecord | None:
        sessions = self._sessions()
        try:
            async with sessions() as session:
                row = await session.scalar(
                    select(MemoryRow).where(
                        *_identity_conditions(scope, memory_id),
                        self._visibility_condition(),
                    )
                )
                return row_to_memory(row) if row is not None else None
        except MemoryEngineError:
            raise
        except SQLAlchemyError as error:
            self._raise_unavailable("get", error)

    @classmethod
    def _query_conditions(cls, query: MemoryQuery | SemanticQuery) -> list[ColumnElement[bool]]:
        conditions = [*_scope_conditions(query.scope), cls._visibility_condition()]
        if query.kinds:
            conditions.append(MemoryRow.kind.in_(kind.value for kind in query.kinds))
        if query.source is not None:
            conditions.append(MemoryRow.source == query.source)
        if query.session_id is not None:
            conditions.append(MemoryRow.session_id == query.session_id)
        return conditions

    async def query(self, query: MemoryQuery) -> MemoryPage:
        sessions = self._sessions()
        conditions = self._query_conditions(query)
        descending = query.order is SortOrder.NEWEST
        order = (
            (MemoryRow.updated_at.desc(), MemoryRow.memory_id.desc())
            if descending
            else (MemoryRow.updated_at.asc(), MemoryRow.memory_id.asc())
        )
        try:
            async with sessions() as session:
                total = await session.scalar(
                    select(func.count()).select_from(MemoryRow).where(*conditions)
                )
                rows = (
                    await session.scalars(
                        select(MemoryRow)
                        .where(*conditions)
                        .order_by(*order)
                        .offset(query.offset)
                        .limit(query.limit)
                    )
                ).all()
            exact_total = int(total or 0)
            next_offset = (
                query.offset + len(rows) if query.offset + len(rows) < exact_total else None
            )
            return MemoryPage(
                scope=query.scope,
                items=tuple(MemoryMatch(row_to_memory(row)) for row in rows),
                offset=query.offset,
                limit=query.limit,
                next_offset=next_offset,
                total_count=exact_total,
                count_precision=CountPrecision.EXACT,
            )
        except MemoryEngineError:
            raise
        except SQLAlchemyError as error:
            self._raise_unavailable("query", error)

    def _validate_embedding(self, embedding: Embedding | None, *, operation: str) -> None:
        if embedding is not None and embedding.dimension != self._config.embedding_dimension:
            raise CapabilityError(
                operation=operation,
                capability="compatible_embedding_dimension",
            )

    async def semantic_search(
        self,
        query: SemanticQuery,
        *,
        query_embedding: Embedding,
    ) -> MemoryPage:
        sessions = self._sessions()
        if query_embedding.task is not EmbeddingTask.QUERY:
            raise DomainValidationError("semantic search requires a query embedding")
        self._validate_embedding(query_embedding, operation="semantic_search")
        conditions = self._query_conditions(query)
        conditions.extend(
            (
                MemoryRow.embedding.is_not(None),
                MemoryRow.embedding_dimension == self._config.embedding_dimension,
                MemoryRow.embedding_model_id == query_embedding.model_id,
            )
        )
        distance = MemoryRow.embedding.cosine_distance(list(query_embedding.values))
        if query.min_score is not None:
            conditions.append(distance <= 2.0 * (1.0 - query.min_score))
        try:
            async with sessions() as session:
                total = await session.scalar(
                    select(func.count()).select_from(MemoryRow).where(*conditions)
                )
                result = await session.execute(
                    select(MemoryRow, distance.label("distance"))
                    .where(*conditions)
                    .order_by(
                        distance.asc(), MemoryRow.updated_at.desc(), MemoryRow.memory_id.desc()
                    )
                    .offset(query.offset)
                    .limit(query.limit)
                )
                pairs = result.all()
            matches: list[MemoryMatch] = []
            for row, raw_distance in pairs:
                numeric_distance = float(raw_distance)
                score = 0.0 if not math.isfinite(numeric_distance) else 1.0 - numeric_distance / 2.0
                matches.append(MemoryMatch(row_to_memory(row), min(1.0, max(0.0, score))))
            exact_total = int(total or 0)
            next_offset = (
                query.offset + len(matches) if query.offset + len(matches) < exact_total else None
            )
            return MemoryPage(
                scope=query.scope,
                items=tuple(matches),
                offset=query.offset,
                limit=query.limit,
                next_offset=next_offset,
                total_count=exact_total,
                count_precision=CountPrecision.EXACT,
            )
        except MemoryEngineError:
            raise
        except SQLAlchemyError as error:
            self._raise_unavailable("semantic_search", error)

    @staticmethod
    def _barrier_conditions(
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
        session_id: str | None,
    ) -> ColumnElement[bool]:
        tenant = tenant_to_storage(scope.tenant_id)
        common = (
            BarrierRow.tenant_id == tenant,
            BarrierRow.subject_id == scope.subject_id,
        )
        alternatives: list[ColumnElement[bool]] = [
            and_(
                BarrierRow.boundary_type == "memory",
                BarrierRow.namespace == scope.namespace,
                BarrierRow.target_key == memory_id,
                BarrierRow.kind == _ALL_KINDS,
            ),
            and_(
                BarrierRow.boundary_type == "scope",
                BarrierRow.namespace == scope.namespace,
                BarrierRow.target_key == _NO_TARGET,
                BarrierRow.kind.in_((_ALL_KINDS, kind.value)),
            ),
            and_(
                BarrierRow.boundary_type == "subject",
                BarrierRow.namespace == _SUBJECT_NAMESPACE,
                BarrierRow.target_key == _NO_TARGET,
                BarrierRow.kind.in_((_ALL_KINDS, kind.value)),
            ),
        ]
        if session_id is not None:
            alternatives.append(
                and_(
                    BarrierRow.boundary_type == "session",
                    BarrierRow.namespace == scope.namespace,
                    BarrierRow.target_key == session_id,
                    BarrierRow.kind == _ALL_KINDS,
                )
            )
        return and_(*common, or_(*alternatives))

    @classmethod
    async def _effective_watermark(
        cls,
        session: AsyncSession,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
        session_id: str | None = None,
    ) -> datetime | None:
        freshness = await session.scalar(
            select(FreshnessRow.watermark).where(
                FreshnessRow.tenant_id == tenant_to_storage(scope.tenant_id),
                FreshnessRow.subject_id == scope.subject_id,
                FreshnessRow.namespace == scope.namespace,
                FreshnessRow.memory_id == memory_id,
                FreshnessRow.kind == kind.value,
            )
        )
        barriers = (
            await session.scalars(
                select(BarrierRow.barrier_timestamp).where(
                    cls._barrier_conditions(
                        scope=scope,
                        memory_id=memory_id,
                        kind=kind,
                        session_id=session_id,
                    )
                )
            )
        ).all()
        values = [value for value in (freshness, *barriers) if value is not None]
        return max(values) if values else None

    async def freshness_watermark(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
    ) -> datetime | None:
        sessions = self._sessions()
        try:
            async with sessions() as session:
                return await self._effective_watermark(
                    session,
                    scope=scope,
                    memory_id=memory_id,
                    kind=kind,
                )
        except MemoryEngineError:
            raise
        except SQLAlchemyError as error:
            self._raise_unavailable("freshness_watermark", error)

    @staticmethod
    def _boundary_values(
        boundary: MemoryScope | MemorySubject,
    ) -> tuple[str, str, str, str]:
        if isinstance(boundary, MemorySubject):
            return (
                "subject",
                tenant_to_storage(boundary.tenant_id),
                boundary.subject_id,
                _SUBJECT_NAMESPACE,
            )
        return (
            "scope",
            tenant_to_storage(boundary.tenant_id),
            boundary.subject_id,
            boundary.namespace,
        )

    @classmethod
    async def _claim_command(
        cls,
        session: AsyncSession,
        *,
        boundary: MemoryScope | MemorySubject,
        command_key: str,
        payload_digest: str,
        result_type: str,
    ) -> tuple[str, Mapping[str, object]] | None:
        boundary_type, tenant_id, subject_id, namespace = cls._boundary_values(boundary)
        inserted = await session.scalar(
            postgres_insert(IdempotencyRow)
            .values(
                boundary_type=boundary_type,
                tenant_id=tenant_id,
                subject_id=subject_id,
                namespace=namespace,
                command_key=command_key,
                payload_digest=payload_digest,
            )
            .on_conflict_do_nothing()
            .returning(IdempotencyRow.command_key)
        )
        if inserted is not None:
            return None
        row = await session.scalar(
            select(IdempotencyRow)
            .where(
                IdempotencyRow.boundary_type == boundary_type,
                IdempotencyRow.tenant_id == tenant_id,
                IdempotencyRow.subject_id == subject_id,
                IdempotencyRow.namespace == namespace,
                IdempotencyRow.command_key == command_key,
            )
            .with_for_update()
        )
        if (
            row is None
            or row.payload_digest != payload_digest
            or (row.result_type is not None and row.result_type != result_type)
        ):
            raise IdempotencyConflictError(idempotency_key=command_key)
        if row.result_type is None or row.result_json is None:
            raise StoreUnavailableError("idempotency result is incomplete")
        return row.result_type, cast("Mapping[str, object]", row.result_json)

    @classmethod
    async def _commit_command_result(
        cls,
        session: AsyncSession,
        *,
        boundary: MemoryScope | MemorySubject,
        command_key: str,
        result_type: str,
        result_json: Mapping[str, object],
    ) -> None:
        boundary_type, tenant_id, subject_id, namespace = cls._boundary_values(boundary)
        await session.execute(
            update(IdempotencyRow)
            .where(
                IdempotencyRow.boundary_type == boundary_type,
                IdempotencyRow.tenant_id == tenant_id,
                IdempotencyRow.subject_id == subject_id,
                IdempotencyRow.namespace == namespace,
                IdempotencyRow.command_key == command_key,
            )
            .values(result_type=result_type, result_json=dict(result_json))
        )

    @staticmethod
    async def _upsert_freshness(
        session: AsyncSession,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
        timestamp: datetime,
    ) -> None:
        statement = postgres_insert(FreshnessRow).values(
            tenant_id=tenant_to_storage(scope.tenant_id),
            subject_id=scope.subject_id,
            namespace=scope.namespace,
            memory_id=memory_id,
            kind=kind.value,
            watermark=timestamp,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    FreshnessRow.tenant_id,
                    FreshnessRow.subject_id,
                    FreshnessRow.namespace,
                    FreshnessRow.memory_id,
                    FreshnessRow.kind,
                ],
                set_={
                    "watermark": func.greatest(
                        FreshnessRow.watermark,
                        statement.excluded.watermark,
                    )
                },
            )
        )

    @staticmethod
    async def _record_contribution(
        session: AsyncSession,
        *,
        record: MemoryRecord,
        timestamp: datetime,
    ) -> None:
        session_id = record.provenance.session_id
        if session_id is None or record.kind not in (MemoryKind.FACT, MemoryKind.SUMMARY):
            return
        statement = postgres_insert(ContributionRow).values(
            tenant_id=tenant_to_storage(record.scope.tenant_id),
            subject_id=record.scope.subject_id,
            namespace=record.scope.namespace,
            session_id=session_id,
            memory_id=record.id,
            kind=record.kind.value,
            event_timestamp=timestamp,
            deleted_at=None,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    ContributionRow.tenant_id,
                    ContributionRow.subject_id,
                    ContributionRow.namespace,
                    ContributionRow.session_id,
                    ContributionRow.memory_id,
                ],
                set_={
                    "kind": statement.excluded.kind,
                    "event_timestamp": statement.excluded.event_timestamp,
                    "deleted_at": None,
                },
            )
        )

    async def _put_in_session(
        self,
        session: AsyncSession,
        command: ConditionalWrite,
        *,
        lock: bool,
    ) -> PutResult:
        record = command.record
        if lock:
            await self._lock_subject(session, record.scope.subject)
        replay = await self._claim_command(
            session,
            boundary=record.scope,
            command_key=command.idempotency_key,
            payload_digest=command.payload_digest,
            result_type="put",
        )
        if replay is not None:
            _, result_json = replay
            return put_result_from_json(result_json, replayed=True)
        self._validate_embedding(record.embedding, operation="put")
        current = await session.scalar(
            select(MemoryRow)
            .where(*_identity_conditions(record.scope, record.id))
            .with_for_update()
        )
        if current is not None and current.kind != record.kind.value:
            raise DomainValidationError("a conditional write cannot change memory kind")
        watermark = await self._effective_watermark(
            session,
            scope=record.scope,
            memory_id=record.id,
            kind=record.kind,
            session_id=record.provenance.session_id,
        )
        if watermark is not None and command.event_timestamp <= watermark:
            raise StaleEventError(event_timestamp=command.event_timestamp, watermark=watermark)
        visible = current is not None and await self._row_is_visible(session, current)
        if command.expected_version is None:
            if visible:
                raise ConflictError(memory_id=record.id)
            status = PutStatus.CREATED
        else:
            if not visible or current is None or current.version != command.expected_version:
                raise ConflictError(memory_id=record.id)
            status = PutStatus.UPDATED
        version = 1 if current is None else current.version + 1
        created_at = record.created_at if current is None else current.created_at
        values = memory_values(record, version=version, created_at=created_at)
        memory_table = cast("Table", MemoryRow.__table__)
        if current is None:
            written = await session.scalar(
                postgres_insert(memory_table)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(memory_table.c.version)
            )
            if written is None:
                raise ConflictError(memory_id=record.id)
        else:
            cursor = cast(
                "CursorResult[object]",
                await session.execute(
                    update(memory_table)
                    .where(
                        memory_table.c.tenant_id == tenant_to_storage(record.scope.tenant_id),
                        memory_table.c.subject_id == record.scope.subject_id,
                        memory_table.c.namespace == record.scope.namespace,
                        memory_table.c.memory_id == record.id,
                        memory_table.c.version == current.version,
                    )
                    .values(**values)
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictError(memory_id=record.id)
        await self._upsert_freshness(
            session,
            scope=record.scope,
            memory_id=record.id,
            kind=record.kind,
            timestamp=command.event_timestamp,
        )
        await self._record_contribution(session, record=record, timestamp=command.event_timestamp)
        result = PutResult((PutOutcome(record.id, record.scope, status, version),))
        await self._commit_command_result(
            session,
            boundary=record.scope,
            command_key=command.idempotency_key,
            result_type="put",
            result_json=put_result_to_json(result),
        )
        return result

    async def put(self, command: ConditionalWrite) -> PutResult:
        sessions = self._sessions()
        try:
            async with sessions.begin() as session:
                return await self._put_in_session(session, command, lock=True)
        except MemoryEngineError:
            raise
        except SQLAlchemyError as error:
            self._raise_unavailable("put", error)

    async def put_many(
        self,
        commands: Sequence[ConditionalWrite],
        *,
        atomic: bool = False,
    ) -> PutResult:
        self._sessions()
        if not commands:
            return PutResult()
        if not atomic:
            outcomes: list[PutOutcome] = []
            for command in commands:
                outcomes.extend((await self.put(command)).outcomes)
            return PutResult(tuple(outcomes))
        subjects = sorted(
            {command.record.scope.subject for command in commands},
            key=_subject_lock_key,
        )
        sessions = self._sessions()
        try:
            async with sessions.begin() as session:
                for subject in subjects:
                    await self._lock_subject(session, subject)
                outcomes = []
                for command in commands:
                    outcomes.extend(
                        (await self._put_in_session(session, command, lock=False)).outcomes
                    )
                return PutResult(tuple(outcomes))
        except MemoryEngineError:
            raise
        except SQLAlchemyError as error:
            self._raise_unavailable("put_many", error)

    @staticmethod
    async def _advance_barrier(
        session: AsyncSession,
        *,
        boundary_type: str,
        tenant_id: str,
        subject_id: str,
        namespace: str,
        target_key: str,
        kind: str,
        timestamp: datetime,
    ) -> datetime:
        statement = postgres_insert(BarrierRow).values(
            boundary_type=boundary_type,
            tenant_id=tenant_id,
            subject_id=subject_id,
            namespace=namespace,
            target_key=target_key,
            kind=kind,
            barrier_timestamp=timestamp,
        )
        value = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=[
                    BarrierRow.boundary_type,
                    BarrierRow.tenant_id,
                    BarrierRow.subject_id,
                    BarrierRow.namespace,
                    BarrierRow.target_key,
                    BarrierRow.kind,
                ],
                set_={
                    "barrier_timestamp": func.greatest(
                        BarrierRow.barrier_timestamp,
                        statement.excluded.barrier_timestamp,
                    )
                },
            ).returning(BarrierRow.barrier_timestamp)
        )
        if value is None:
            raise StoreUnavailableError("deletion barrier was not persisted")
        return value

    @classmethod
    async def _validate_delete_freshness(
        cls,
        session: AsyncSession,
        *,
        event_timestamp: datetime,
        records: Sequence[MemoryRow],
        contributions: Sequence[ContributionRow],
    ) -> None:
        candidates = [contribution.event_timestamp for contribution in contributions]
        for row in records:
            scope = MemoryScope(
                tenant_id=row.tenant_id or None,
                subject_id=row.subject_id,
                namespace=row.namespace,
            )
            value = await cls._effective_watermark(
                session,
                scope=scope,
                memory_id=row.memory_id,
                kind=MemoryKind(row.kind),
                session_id=row.session_id,
            )
            if value is not None:
                candidates.append(value)
        watermark = max(candidates) if candidates else None
        if watermark is not None and event_timestamp <= watermark:
            raise StaleEventError(event_timestamp=event_timestamp, watermark=watermark)

    async def _delete_in_session(
        self,
        session: AsyncSession,
        command: DeleteCommand,
    ) -> DeleteResult:
        boundary: MemoryScope | MemorySubject = (
            command.subject if isinstance(command, DeleteSubjectCommand) else command.scope
        )
        subject = boundary if isinstance(boundary, MemorySubject) else boundary.subject
        await self._lock_subject(session, subject)
        replay = await self._claim_command(
            session,
            boundary=boundary,
            command_key=command.idempotency_key,
            payload_digest=command.payload_digest,
            result_type="delete",
        )
        if replay is not None:
            _, result_json = replay
            return delete_result_from_json(result_json, replayed=True)

        records: Sequence[MemoryRow] = ()
        contributions: Sequence[ContributionRow] = ()
        already_absent = 0
        if isinstance(command, DeleteMemoryCommand):
            row = await session.scalar(
                select(MemoryRow)
                .where(*_identity_conditions(command.scope, command.memory_id))
                .with_for_update()
            )
            records = (row,) if row is not None else ()
            already_absent = 0 if records else 1
        elif isinstance(command, DeleteScopeCommand):
            conditions = [*_scope_conditions(command.scope)]
            if command.kinds:
                conditions.append(MemoryRow.kind.in_(kind.value for kind in command.kinds))
            records = (
                await session.scalars(select(MemoryRow).where(*conditions).with_for_update())
            ).all()
        elif isinstance(command, DeleteSubjectCommand):
            conditions = [
                MemoryRow.tenant_id == tenant_to_storage(command.subject.tenant_id),
                MemoryRow.subject_id == command.subject.subject_id,
            ]
            if command.kinds:
                conditions.append(MemoryRow.kind.in_(kind.value for kind in command.kinds))
            records = (
                await session.scalars(select(MemoryRow).where(*conditions).with_for_update())
            ).all()
        elif isinstance(command, DeleteSessionCommand):
            contributions = (
                await session.scalars(
                    select(ContributionRow)
                    .where(
                        ContributionRow.tenant_id == tenant_to_storage(command.scope.tenant_id),
                        ContributionRow.subject_id == command.scope.subject_id,
                        ContributionRow.namespace == command.scope.namespace,
                        ContributionRow.session_id == command.session_id,
                        ContributionRow.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
            records = (
                await session.scalars(
                    select(MemoryRow)
                    .where(
                        *_scope_conditions(command.scope),
                        MemoryRow.session_id == command.session_id,
                        MemoryRow.kind.not_in((MemoryKind.FACT.value, MemoryKind.SUMMARY.value)),
                    )
                    .with_for_update()
                )
            ).all()

        await self._validate_delete_freshness(
            session,
            event_timestamp=command.event_timestamp,
            records=records,
            contributions=contributions,
        )
        for row in records:
            await session.delete(row)
        if contributions:
            identities = [
                and_(
                    ContributionRow.tenant_id == row.tenant_id,
                    ContributionRow.subject_id == row.subject_id,
                    ContributionRow.namespace == row.namespace,
                    ContributionRow.session_id == row.session_id,
                    ContributionRow.memory_id == row.memory_id,
                )
                for row in contributions
            ]
            await session.execute(
                update(ContributionRow)
                .where(or_(*identities), ContributionRow.deleted_at.is_(None))
                .values(deleted_at=command.event_timestamp)
            )

        timestamp = command.event_timestamp
        if isinstance(command, DeleteMemoryCommand):
            timestamp = await self._advance_barrier(
                session,
                boundary_type="memory",
                tenant_id=tenant_to_storage(command.scope.tenant_id),
                subject_id=command.scope.subject_id,
                namespace=command.scope.namespace,
                target_key=command.memory_id,
                kind=_ALL_KINDS,
                timestamp=timestamp,
            )
        elif isinstance(command, DeleteScopeCommand):
            kinds = tuple(kind.value for kind in command.kinds) or (_ALL_KINDS,)
            for kind in kinds:
                value = await self._advance_barrier(
                    session,
                    boundary_type="scope",
                    tenant_id=tenant_to_storage(command.scope.tenant_id),
                    subject_id=command.scope.subject_id,
                    namespace=command.scope.namespace,
                    target_key=_NO_TARGET,
                    kind=kind,
                    timestamp=command.event_timestamp,
                )
                timestamp = max(timestamp, value)
        elif isinstance(command, DeleteSubjectCommand):
            kinds = tuple(kind.value for kind in command.kinds) or (_ALL_KINDS,)
            for kind in kinds:
                value = await self._advance_barrier(
                    session,
                    boundary_type="subject",
                    tenant_id=tenant_to_storage(command.subject.tenant_id),
                    subject_id=command.subject.subject_id,
                    namespace=_SUBJECT_NAMESPACE,
                    target_key=_NO_TARGET,
                    kind=kind,
                    timestamp=command.event_timestamp,
                )
                timestamp = max(timestamp, value)
        elif isinstance(command, DeleteSessionCommand):
            timestamp = await self._advance_barrier(
                session,
                boundary_type="session",
                tenant_id=tenant_to_storage(command.scope.tenant_id),
                subject_id=command.scope.subject_id,
                namespace=command.scope.namespace,
                target_key=command.session_id,
                kind=_ALL_KINDS,
                timestamp=timestamp,
            )

        result = DeleteResult(
            matched_count=len(records) + len(contributions),
            hard_deleted_count=len(records),
            contribution_deleted_count=len(contributions),
            tombstoned_count=len(contributions),
            already_absent_count=already_absent,
            barrier_timestamp=timestamp,
        )
        await self._commit_command_result(
            session,
            boundary=boundary,
            command_key=command.idempotency_key,
            result_type="delete",
            result_json=delete_result_to_json(result),
        )
        return result

    async def delete(self, command: DeleteCommand) -> DeleteResult:
        sessions = self._sessions()
        try:
            async with sessions.begin() as session:
                return await self._delete_in_session(session, command)
        except MemoryEngineError:
            raise
        except SQLAlchemyError as error:
            self._raise_unavailable("delete", error)
