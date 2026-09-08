from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from portable_memory_engine.adapters.postgres import (
    PostgresMemoryStore,
    PostgresStoreConfig,
    downgrade_postgres_schema,
    upgrade_postgres_schema,
)
from portable_memory_engine.domain import (
    ConditionalWrite,
    ConflictError,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    MemoryScope,
    PutResult,
)
from portable_memory_engine.ports import MemoryStore
from portable_memory_engine.testing.contracts import (
    MemoryStoreContractFactory,
    MemoryStoreContractSuite,
)

pytestmark = pytest.mark.postgres

_BASE_TIME = datetime(2026, 7, 31, tzinfo=UTC)
_PME_TABLES = (
    "pme_idempotency",
    "pme_session_contributions",
    "pme_deletion_barriers",
    "pme_freshness",
    "pme_memories",
    "pme_store_config",
)


@pytest.fixture(scope="module")
def postgres_config() -> PostgresStoreConfig:
    database_url = os.environ.get("PME_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PME_TEST_DATABASE_URL is not configured")
    return PostgresStoreConfig(database_url, embedding_dimension=2)


async def _table_names(config: PostgresStoreConfig) -> set[str]:
    engine = create_async_engine(config.sqlalchemy_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def migrated_database(postgres_config: PostgresStoreConfig) -> Iterator[None]:
    async def prepare() -> None:
        await downgrade_postgres_schema(postgres_config)
        assert not set(_PME_TABLES) & await _table_names(postgres_config)
        await upgrade_postgres_schema(postgres_config)
        assert set(_PME_TABLES) <= await _table_names(postgres_config)
        await downgrade_postgres_schema(postgres_config)
        assert not set(_PME_TABLES) & await _table_names(postgres_config)
        await upgrade_postgres_schema(postgres_config)

    asyncio.run(prepare())
    yield
    asyncio.run(downgrade_postgres_schema(postgres_config))


@pytest.fixture(autouse=True)
def clean_database(
    postgres_config: PostgresStoreConfig,
    migrated_database: None,
) -> None:
    async def clean() -> None:
        engine = create_async_engine(postgres_config.sqlalchemy_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("TRUNCATE TABLE " + ", ".join(_PME_TABLES) + " CASCADE")
                )
        finally:
            await engine.dispose()

    asyncio.run(clean())


@pytest.fixture
def memory_store_factory(
    postgres_config: PostgresStoreConfig,
) -> MemoryStoreContractFactory:
    return lambda: PostgresMemoryStore(postgres_config)


class TestPostgresMemoryStoreContract(MemoryStoreContractSuite):
    """Run the same reusable contract used by the in-memory adapter."""


def _scope() -> MemoryScope:
    return MemoryScope(tenant_id="tenant-1", subject_id="subject-1", namespace="integration")


def _record(memory_id: str, timestamp: datetime = _BASE_TIME) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=_scope(),
        kind=MemoryKind.FACT,
        content=f"synthetic-{memory_id}",
        created_at=timestamp,
        updated_at=timestamp,
        provenance=MemoryProvenance(
            source="integration",
            session_id="session-1",
            event_timestamp=timestamp,
        ),
        embedding=Embedding((1.0, 0.0), "embedding-1", EmbeddingTask.DOCUMENT),
    )


def _write(
    record: MemoryRecord,
    *,
    key: str,
    timestamp: datetime,
    expected_version: str | int | None = None,
) -> ConditionalWrite:
    return ConditionalWrite(
        record=record,
        idempotency_key=key,
        payload_digest=f"digest:{key}",
        event_timestamp=timestamp,
        expected_version=expected_version,
    )


async def _with_store(
    config: PostgresStoreConfig,
    operation: Callable[[PostgresMemoryStore], Awaitable[None]],
) -> None:
    store = PostgresMemoryStore(config)
    await store.open()
    try:
        await operation(store)
    finally:
        await store.close()


def test_migration_round_trip_was_verified(migrated_database: None) -> None:
    assert migrated_database is None


def test_postgres_store_satisfies_runtime_protocol(
    postgres_config: PostgresStoreConfig,
) -> None:
    assert isinstance(PostgresMemoryStore(postgres_config), MemoryStore)


def test_concurrent_create_uniqueness(postgres_config: PostgresStoreConfig) -> None:
    async def scenario(store: PostgresMemoryStore) -> None:
        first = _write(_record("unique"), key="unique-a", timestamp=_BASE_TIME)
        second = _write(
            _record("unique", _BASE_TIME + timedelta(seconds=1)),
            key="unique-b",
            timestamp=_BASE_TIME + timedelta(seconds=1),
        )
        results = await asyncio.gather(store.put(first), store.put(second), return_exceptions=True)
        assert sum(isinstance(result, PutResult) for result in results) == 1
        assert sum(isinstance(result, ConflictError) for result in results) == 1

    asyncio.run(_with_store(postgres_config, scenario))


def test_concurrent_compare_and_swap(postgres_config: PostgresStoreConfig) -> None:
    async def scenario(store: PostgresMemoryStore) -> None:
        created = await store.put(_write(_record("cas"), key="cas-create", timestamp=_BASE_TIME))
        version = created.outcomes[0].version
        first_time = _BASE_TIME + timedelta(seconds=1)
        second_time = _BASE_TIME + timedelta(seconds=2)
        base = _record("cas")
        commands = (
            _write(
                replace(base, content="first", updated_at=first_time),
                key="cas-a",
                timestamp=first_time,
                expected_version=version,
            ),
            _write(
                replace(base, content="second", updated_at=second_time),
                key="cas-b",
                timestamp=second_time,
                expected_version=version,
            ),
        )
        results = await asyncio.gather(
            *(store.put(command) for command in commands), return_exceptions=True
        )
        assert sum(isinstance(result, PutResult) for result in results) == 1
        assert sum(isinstance(result, ConflictError) for result in results) == 1

    asyncio.run(_with_store(postgres_config, scenario))


def test_atomic_group_rolls_back_on_conflict(postgres_config: PostgresStoreConfig) -> None:
    async def scenario(store: PostgresMemoryStore) -> None:
        await store.put(_write(_record("existing"), key="existing", timestamp=_BASE_TIME))
        timestamp = _BASE_TIME + timedelta(seconds=1)
        commands = (
            _write(_record("rolled-back", timestamp), key="rollback-first", timestamp=timestamp),
            _write(_record("existing", timestamp), key="rollback-conflict", timestamp=timestamp),
        )
        with pytest.raises(ConflictError):
            await store.put_many(commands, atomic=True)
        assert await store.get(scope=_scope(), memory_id="rolled-back") is None

    asyncio.run(_with_store(postgres_config, scenario))


def test_embedding_dimension_is_persisted_and_revalidated(
    postgres_config: PostgresStoreConfig,
) -> None:
    async def scenario() -> None:
        first = PostgresMemoryStore(postgres_config)
        await first.open()
        await first.close()
        mismatched = PostgresMemoryStore(replace(postgres_config, embedding_dimension=3))
        with pytest.raises(DomainValidationError, match="embedding_dimension"):
            await mismatched.open()

    asyncio.run(scenario())
