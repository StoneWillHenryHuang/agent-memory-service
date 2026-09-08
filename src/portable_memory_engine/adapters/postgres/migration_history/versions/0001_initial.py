"""Create the provider-neutral PostgreSQL and pgvector storage schema.

Revision ID: pme_0001
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "pme_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "pme_store_config",
        sa.Column("config_key", sa.Text(), primary_key=True),
        sa.Column("integer_value", sa.Integer(), nullable=False),
    )
    op.create_table(
        "pme_memories",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), primary_key=True),
        sa.Column("namespace", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("provenance_event_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_digest", sa.Text(), nullable=True),
        sa.Column("provenance_model_id", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("embedding_model_id", sa.Text(), nullable=True),
        sa.Column("embedding_task", sa.Text(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "(embedding IS NULL AND embedding_dimension IS NULL "
            "AND embedding_model_id IS NULL AND embedding_task IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_dimension = vector_dims(embedding) "
            "AND embedding_model_id IS NOT NULL AND embedding_task IS NOT NULL)",
            name="ck_pme_memories_embedding_coherent",
        ),
    )
    op.create_index(
        "ix_pme_memories_scope_updated",
        "pme_memories",
        ["tenant_id", "subject_id", "namespace", sa.text("updated_at DESC"), "memory_id"],
    )
    op.create_index(
        "ix_pme_memories_scope_source",
        "pme_memories",
        ["tenant_id", "subject_id", "namespace", "source"],
    )
    op.create_index(
        "ix_pme_memories_scope_session",
        "pme_memories",
        ["tenant_id", "subject_id", "namespace", "session_id"],
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.create_table(
        "pme_freshness",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), primary_key=True),
        sa.Column("namespace", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("watermark", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "pme_deletion_barriers",
        sa.Column("boundary_type", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), primary_key=True),
        sa.Column("namespace", sa.Text(), primary_key=True),
        sa.Column("target_key", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("barrier_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "boundary_type IN ('memory', 'scope', 'subject', 'session')",
            name="ck_pme_deletion_barriers_type",
        ),
    )
    op.create_table(
        "pme_session_contributions",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), primary_key=True),
        sa.Column("namespace", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "subject_id", "namespace", "memory_id"],
            [
                "pme_memories.tenant_id",
                "pme_memories.subject_id",
                "pme_memories.namespace",
                "pme_memories.memory_id",
            ],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_pme_contributions_deleted_memory",
        "pme_session_contributions",
        ["tenant_id", "subject_id", "namespace", "memory_id"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_index(
        "ix_pme_contributions_session",
        "pme_session_contributions",
        ["tenant_id", "subject_id", "namespace", "session_id"],
    )
    op.create_table(
        "pme_idempotency",
        sa.Column("boundary_type", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), primary_key=True),
        sa.Column("namespace", sa.Text(), primary_key=True),
        sa.Column("command_key", sa.Text(), primary_key=True),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("result_type", sa.Text(), nullable=True),
        sa.Column("result_json", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "boundary_type IN ('scope', 'subject')",
            name="ck_pme_idempotency_boundary_type",
        ),
        sa.CheckConstraint(
            "result_type IS NULL OR result_type IN ('put', 'delete')",
            name="ck_pme_idempotency_result_type",
        ),
        sa.UniqueConstraint(
            "boundary_type",
            "tenant_id",
            "subject_id",
            "namespace",
            "command_key",
            name="uq_pme_idempotency_boundary_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("pme_idempotency")
    op.drop_table("pme_session_contributions")
    op.drop_table("pme_deletion_barriers")
    op.drop_table("pme_freshness")
    op.drop_table("pme_memories")
    op.drop_table("pme_store_config")
