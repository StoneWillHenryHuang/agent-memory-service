"""Alembic environment requiring a caller-owned SQLAlchemy connection."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from portable_memory_engine.adapters.postgres.models import Base


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


provided_connection = context.config.attributes.get("connection")
if not isinstance(provided_connection, Connection):
    raise RuntimeError("PostgreSQL migrations require a programmatic SQLAlchemy connection")
run_migrations(provided_connection)
