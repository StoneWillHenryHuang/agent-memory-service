"""PostgreSQL and pgvector reference storage adapter.

Install ``portable-memory-engine[postgres]`` before importing this module.
"""

from portable_memory_engine.adapters.postgres.config import (
    PostgresStoreConfig,
    PostgresVectorSearch,
)
from portable_memory_engine.adapters.postgres.migrations import (
    downgrade_postgres_schema,
    upgrade_postgres_schema,
)
from portable_memory_engine.adapters.postgres.store import PostgresMemoryStore

__all__ = (
    "PostgresMemoryStore",
    "PostgresStoreConfig",
    "PostgresVectorSearch",
    "downgrade_postgres_schema",
    "upgrade_postgres_schema",
)
