"""Adapter-private SQLAlchemy models; domain values never depend on these rows."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata root for the independent public migration history."""


class StoreConfigRow(Base):
    __tablename__ = "pme_store_config"

    config_key: Mapped[str] = mapped_column(Text, primary_key=True)
    integer_value: Mapped[int] = mapped_column(Integer, nullable=False)


class MemoryRow(Base):
    __tablename__ = "pme_memories"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(Text, primary_key=True)
    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    memory_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_event_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    input_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_model_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_model_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_task: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(embedding IS NULL AND embedding_dimension IS NULL "
            "AND embedding_model_id IS NULL AND embedding_task IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_dimension = vector_dims(embedding) "
            "AND embedding_model_id IS NOT NULL AND embedding_task IS NOT NULL)",
            name="ck_pme_memories_embedding_coherent",
        ),
        Index(
            "ix_pme_memories_scope_updated",
            "tenant_id",
            "subject_id",
            "namespace",
            text("updated_at DESC"),
            "memory_id",
        ),
        Index(
            "ix_pme_memories_scope_source",
            "tenant_id",
            "subject_id",
            "namespace",
            "source",
        ),
        Index(
            "ix_pme_memories_scope_session",
            "tenant_id",
            "subject_id",
            "namespace",
            "session_id",
            postgresql_where=text("session_id IS NOT NULL"),
        ),
    )


class FreshnessRow(Base):
    __tablename__ = "pme_freshness"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(Text, primary_key=True)
    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    memory_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    watermark: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BarrierRow(Base):
    __tablename__ = "pme_deletion_barriers"

    boundary_type: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(Text, primary_key=True)
    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    target_key: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    barrier_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "boundary_type IN ('memory', 'scope', 'subject', 'session')",
            name="ck_pme_deletion_barriers_type",
        ),
    )


class ContributionRow(Base):
    __tablename__ = "pme_session_contributions"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(Text, primary_key=True)
    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    memory_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "subject_id", "namespace", "memory_id"],
            [
                "pme_memories.tenant_id",
                "pme_memories.subject_id",
                "pme_memories.namespace",
                "pme_memories.memory_id",
            ],
            ondelete="CASCADE",
        ),
        Index(
            "ix_pme_contributions_deleted_memory",
            "tenant_id",
            "subject_id",
            "namespace",
            "memory_id",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
        Index(
            "ix_pme_contributions_session",
            "tenant_id",
            "subject_id",
            "namespace",
            "session_id",
        ),
    )


class IdempotencyRow(Base):
    __tablename__ = "pme_idempotency"

    boundary_type: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(Text, primary_key=True)
    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    command_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload_digest: Mapped[str] = mapped_column(Text, nullable=False)
    result_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "boundary_type IN ('scope', 'subject')",
            name="ck_pme_idempotency_boundary_type",
        ),
        CheckConstraint(
            "result_type IS NULL OR result_type IN ('put', 'delete')",
            name="ck_pme_idempotency_result_type",
        ),
        UniqueConstraint(
            "boundary_type",
            "tenant_id",
            "subject_id",
            "namespace",
            "command_key",
            name="uq_pme_idempotency_boundary_key",
        ),
    )
