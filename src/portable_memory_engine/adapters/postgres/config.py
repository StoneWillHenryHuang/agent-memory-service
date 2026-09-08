"""Validated, credential-safe PostgreSQL adapter configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from portable_memory_engine.domain import DomainValidationError


class PostgresVectorSearch(StrEnum):
    """Supported public pgvector search strategies."""

    EXACT = "exact"


@dataclass(frozen=True, slots=True, repr=False)
class PostgresStoreConfig:
    """Connection and schema configuration without provider-specific tuning."""

    database_url: str = field(repr=False)
    embedding_dimension: int
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout_seconds: float = 30.0
    search: PostgresVectorSearch = PostgresVectorSearch.EXACT
    auto_migrate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.database_url, str) or not self.database_url.strip():
            raise DomainValidationError("database_url must be a non-empty string")
        try:
            url = make_url(self.database_url)
        except ArgumentError:
            raise DomainValidationError("database_url must be a valid SQLAlchemy URL") from None
        if url.drivername not in {
            "postgres",
            "postgresql",
            "postgresql+psycopg",
            "postgresql+psycopg_async",
        }:
            raise DomainValidationError("database_url must use PostgreSQL with psycopg")
        if (
            isinstance(self.embedding_dimension, bool)
            or not isinstance(self.embedding_dimension, int)
            or not 1 <= self.embedding_dimension <= 16_000
        ):
            raise DomainValidationError("embedding_dimension must be between 1 and 16000")
        if (
            isinstance(self.pool_size, bool)
            or not isinstance(self.pool_size, int)
            or self.pool_size < 1
        ):
            raise DomainValidationError("pool_size must be a positive integer")
        if (
            isinstance(self.max_overflow, bool)
            or not isinstance(self.max_overflow, int)
            or self.max_overflow < 0
        ):
            raise DomainValidationError("max_overflow must be a non-negative integer")
        if (
            isinstance(self.pool_timeout_seconds, bool)
            or not isinstance(self.pool_timeout_seconds, (int, float))
            or self.pool_timeout_seconds <= 0
        ):
            raise DomainValidationError("pool_timeout_seconds must be positive")
        if not isinstance(self.search, PostgresVectorSearch):
            raise DomainValidationError("search must be PostgresVectorSearch")
        if not isinstance(self.auto_migrate, bool):
            raise DomainValidationError("auto_migrate must be boolean")

    @property
    def sqlalchemy_url(self) -> URL:
        """Return a psycopg async URL without exposing it through repr."""

        url = make_url(self.database_url)
        if url.drivername in {"postgres", "postgresql"}:
            return url.set(drivername="postgresql+psycopg")
        if url.drivername == "postgresql+psycopg_async":
            return url.set(drivername="postgresql+psycopg")
        return url

    @property
    def redacted_database_url(self) -> str:
        """Return a URL safe for diagnostics, with any password hidden."""

        return self.sqlalchemy_url.render_as_string(hide_password=True)

    def __repr__(self) -> str:
        return (
            "PostgresStoreConfig(database_url=<redacted>, "
            f"embedding_dimension={self.embedding_dimension}, pool_size={self.pool_size}, "
            f"max_overflow={self.max_overflow}, "
            f"pool_timeout_seconds={self.pool_timeout_seconds}, search={self.search!r}, "
            f"auto_migrate={self.auto_migrate})"
        )
