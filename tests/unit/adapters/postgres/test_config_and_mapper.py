from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from portable_memory_engine.adapters.postgres.config import PostgresStoreConfig
from portable_memory_engine.adapters.postgres.mapper import (
    delete_result_from_json,
    delete_result_to_json,
    memory_values,
    put_result_from_json,
    put_result_to_json,
    row_to_memory,
    tenant_from_storage,
    tenant_to_storage,
)
from portable_memory_engine.adapters.postgres.models import MemoryRow
from portable_memory_engine.adapters.postgres.store import PostgresMemoryStore
from portable_memory_engine.domain import (
    DeleteResult,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    MemoryScope,
    PutOutcome,
    PutResult,
    PutStatus,
    StoreUnavailableError,
)

_NOW = datetime(2026, 7, 31, tzinfo=UTC)


def _record() -> MemoryRecord:
    return MemoryRecord(
        id="memory-1",
        scope=MemoryScope(subject_id="subject-1", namespace="notes"),
        kind=MemoryKind.FACT,
        content="Synthetic preference",
        created_at=_NOW,
        updated_at=_NOW,
        provenance=MemoryProvenance(
            source="synthetic",
            session_id="session-1",
            event_timestamp=_NOW,
            input_digest="digest-1",
            model_id="model-1",
            prompt_version="prompt-1",
            schema_version="schema-1",
        ),
        metadata={"labels": ("synthetic", 1)},
        embedding=Embedding(
            values=(1.0, 0.0, 0.0),
            model_id="embedding-1",
            task=EmbeddingTask.DOCUMENT,
        ),
    )


def test_config_redacts_credentials_and_normalizes_driver() -> None:
    config = PostgresStoreConfig(
        "postgresql://synthetic-user:do-not-log@localhost/synthetic-db",
        embedding_dimension=3,
    )

    assert config.sqlalchemy_url.drivername == "postgresql+psycopg"
    assert "do-not-log" not in repr(config)
    assert "do-not-log" not in config.redacted_database_url
    assert "***" in config.redacted_database_url


@pytest.mark.parametrize(
    ("url", "dimension"),
    [
        ("sqlite:///synthetic.db", 3),
        ("postgresql://localhost/synthetic", 0),
        ("postgresql://localhost/synthetic", 16_001),
    ],
)
def test_config_rejects_unsupported_urls_and_dimensions(url: str, dimension: int) -> None:
    with pytest.raises(DomainValidationError):
        PostgresStoreConfig(url, embedding_dimension=dimension)


def test_native_database_errors_do_not_escape_through_causes_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "synthetic-sensitive-database-marker"
    with pytest.raises(DomainValidationError) as invalid_url:
        PostgresStoreConfig("://", embedding_dimension=3)
    assert invalid_url.value.__cause__ is None

    caplog.set_level(logging.DEBUG)
    with pytest.raises(StoreUnavailableError) as unavailable:
        PostgresMemoryStore._raise_unavailable("get", RuntimeError(marker))
    assert unavailable.value.__cause__ is None
    assert marker not in repr(unavailable.value)
    assert marker not in caplog.text


def test_mapper_round_trips_domain_values_without_leaking_tenant_sentinel() -> None:
    record = _record()
    values = memory_values(record, version=7, created_at=record.created_at)
    row = MemoryRow(
        tenant_id=str(values["tenant_id"]),
        subject_id=str(values["subject_id"]),
        namespace=str(values["namespace"]),
        memory_id=str(values["memory_id"]),
        kind=str(values["kind"]),
        content=str(values["content"]),
        created_at=record.created_at,
        updated_at=record.updated_at,
        source=record.provenance.source,
        session_id=record.provenance.session_id,
        provenance_event_timestamp=record.provenance.event_timestamp,
        input_digest=record.provenance.input_digest,
        provenance_model_id=record.provenance.model_id,
        prompt_version=record.provenance.prompt_version,
        schema_version=record.provenance.schema_version,
        metadata_json={"labels": ["synthetic", 1]},
        embedding=[1.0, 0.0, 0.0],
        embedding_dimension=3,
        embedding_model_id="embedding-1",
        embedding_task="document",
        version=7,
    )

    restored = row_to_memory(row)

    assert restored == replace(record, version=7)
    assert tenant_to_storage(None) == ""
    assert tenant_from_storage(tenant_to_storage(None)) is None


def test_idempotency_results_round_trip_with_replay_markers() -> None:
    record = _record()
    put_result = PutResult((PutOutcome(record.id, record.scope, PutStatus.CREATED, 9),))
    replayed_put = put_result_from_json(put_result_to_json(put_result), replayed=True)
    delete_result = DeleteResult(1, 1, 0, 0, 0, _NOW)
    replayed_delete = delete_result_from_json(delete_result_to_json(delete_result), replayed=True)

    assert replayed_put.outcomes[0].replayed is True
    assert replayed_put.outcomes[0].version == 9
    assert replayed_delete.replayed is True
    assert replayed_delete.deleted_count == 1
