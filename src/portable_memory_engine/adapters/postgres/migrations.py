"""Programmatic Alembic runner for the adapter's independent migration history."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from portable_memory_engine.adapters.postgres.config import PostgresStoreConfig


def _alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migration_history")))
    config.attributes["connection"] = connection
    return config


async def _run(
    config: PostgresStoreConfig, operation: Callable[[Config, str], None], target: str
) -> None:
    engine = create_async_engine(
        config.sqlalchemy_url,
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: operation(_alembic_config(sync_connection), target)
            )
    finally:
        await engine.dispose()


async def upgrade_postgres_schema(config: PostgresStoreConfig, revision: str = "head") -> None:
    """Upgrade a database without logging or stringifying its credential-bearing URL."""

    await _run(config, command.upgrade, revision)


async def downgrade_postgres_schema(config: PostgresStoreConfig, revision: str = "base") -> None:
    """Downgrade adapter tables; the shared pgvector extension is intentionally retained."""

    await _run(config, command.downgrade, revision)
