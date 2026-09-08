"""Explicit translation between immutable domain values and PostgreSQL rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from portable_memory_engine.adapters.postgres.models import MemoryRow
from portable_memory_engine.domain import (
    DeleteResult,
    Embedding,
    EmbeddingTask,
    FrozenJsonObject,
    JsonValue,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    MemoryScope,
    PutOutcome,
    PutResult,
    PutStatus,
)

_NO_TENANT = ""


def tenant_to_storage(tenant_id: str | None) -> str:
    """Encode the concrete no-tenant partition without leaking it to the domain."""

    return tenant_id if tenant_id is not None else _NO_TENANT


def tenant_from_storage(tenant_id: str) -> str | None:
    """Decode the adapter-private no-tenant representation."""

    return tenant_id or None


def _json_value(value: JsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def metadata_to_storage(metadata: Mapping[str, JsonValue]) -> dict[str, object]:
    """Return a mutable JSON object suitable for JSONB binding."""

    return {key: _json_value(value) for key, value in metadata.items()}


def memory_values(
    record: MemoryRecord,
    *,
    version: int,
    created_at: datetime,
) -> dict[str, object]:
    """Map a record to column values while keeping persistence fields explicit."""

    embedding = record.embedding
    return {
        "tenant_id": tenant_to_storage(record.scope.tenant_id),
        "subject_id": record.scope.subject_id,
        "namespace": record.scope.namespace,
        "memory_id": record.id,
        "kind": record.kind.value,
        "content": record.content,
        "created_at": created_at,
        "updated_at": record.updated_at,
        "source": record.provenance.source,
        "session_id": record.provenance.session_id,
        "provenance_event_timestamp": record.provenance.event_timestamp,
        "input_digest": record.provenance.input_digest,
        "provenance_model_id": record.provenance.model_id,
        "prompt_version": record.provenance.prompt_version,
        "schema_version": record.provenance.schema_version,
        "metadata": metadata_to_storage(record.metadata),
        "embedding": list(embedding.values) if embedding is not None else None,
        "embedding_dimension": embedding.dimension if embedding is not None else None,
        "embedding_model_id": embedding.model_id if embedding is not None else None,
        "embedding_task": embedding.task.value if embedding is not None else None,
        "version": version,
    }


def row_to_memory(row: MemoryRow) -> MemoryRecord:
    """Reconstruct and revalidate a domain record from one ORM row."""

    embedding = None
    if row.embedding is not None:
        values = tuple(float(value) for value in cast("Sequence[float]", row.embedding))
        embedding = Embedding(
            values=values,
            model_id=cast("str", row.embedding_model_id),
            task=EmbeddingTask(cast("str", row.embedding_task)),
        )
    metadata = cast("Mapping[str, JsonValue]", row.metadata_json)
    return MemoryRecord(
        id=row.memory_id,
        scope=MemoryScope(
            tenant_id=tenant_from_storage(row.tenant_id),
            subject_id=row.subject_id,
            namespace=row.namespace,
        ),
        kind=MemoryKind(row.kind),
        content=row.content,
        created_at=row.created_at,
        updated_at=row.updated_at,
        provenance=MemoryProvenance(
            source=row.source,
            session_id=row.session_id,
            event_timestamp=row.provenance_event_timestamp,
            input_digest=row.input_digest,
            model_id=row.provenance_model_id,
            prompt_version=row.prompt_version,
            schema_version=row.schema_version,
        ),
        metadata=metadata,
        embedding=embedding,
        version=row.version,
    )


def scope_to_json(scope: MemoryScope) -> dict[str, object]:
    return {
        "tenant_id": scope.tenant_id,
        "subject_id": scope.subject_id,
        "namespace": scope.namespace,
    }


def scope_from_json(value: object) -> MemoryScope:
    data = cast("dict[str, object]", value)
    return MemoryScope(
        tenant_id=cast("str | None", data["tenant_id"]),
        subject_id=cast("str", data["subject_id"]),
        namespace=cast("str", data["namespace"]),
    )


def put_result_to_json(result: PutResult) -> dict[str, object]:
    return {
        "outcomes": [
            {
                "memory_id": outcome.memory_id,
                "scope": scope_to_json(outcome.scope),
                "status": outcome.status.value,
                "version": outcome.version,
            }
            for outcome in result.outcomes
        ]
    }


def put_result_from_json(value: Mapping[str, object], *, replayed: bool) -> PutResult:
    items = cast("Sequence[dict[str, object]]", value["outcomes"])
    return PutResult(
        tuple(
            PutOutcome(
                memory_id=cast("str", item["memory_id"]),
                scope=scope_from_json(item["scope"]),
                status=PutStatus(cast("str", item["status"])),
                version=cast("str | int", item["version"]),
                replayed=replayed,
            )
            for item in items
        )
    )


def delete_result_to_json(result: DeleteResult) -> dict[str, object]:
    return {
        "matched_count": result.matched_count,
        "hard_deleted_count": result.hard_deleted_count,
        "contribution_deleted_count": result.contribution_deleted_count,
        "tombstoned_count": result.tombstoned_count,
        "already_absent_count": result.already_absent_count,
        "barrier_timestamp": result.barrier_timestamp.isoformat(),
    }


def delete_result_from_json(value: Mapping[str, object], *, replayed: bool) -> DeleteResult:
    return DeleteResult(
        matched_count=cast("int", value["matched_count"]),
        hard_deleted_count=cast("int", value["hard_deleted_count"]),
        contribution_deleted_count=cast("int", value["contribution_deleted_count"]),
        tombstoned_count=cast("int", value["tombstoned_count"]),
        already_absent_count=cast("int", value["already_absent_count"]),
        barrier_timestamp=datetime.fromisoformat(cast("str", value["barrier_timestamp"])),
        replayed=replayed,
    )
