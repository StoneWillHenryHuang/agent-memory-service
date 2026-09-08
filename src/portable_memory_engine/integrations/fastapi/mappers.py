"""Explicit HTTP-schema to public-domain mapping for the reference service."""

from __future__ import annotations

from portable_memory_engine import (
    AddMemoryCommand,
    AddMemoryResult,
    ConversationMessage,
    DeleteCommand,
    DeleteMemoryCommand,
    DeleteResult,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    MemoryKind,
    MemoryQuery,
    MemoryScope,
    MemorySubject,
    RecallMode,
    RecallRequest,
    RecallResult,
)
from portable_memory_engine.integrations.fastapi.schemas import (
    AddRequest,
    AddResponse,
    DeleteMemoryRequest,
    DeleteRequest,
    DeleteResponse,
    DeleteScopeRequest,
    DeleteSessionRequest,
    DeleteSubjectRequest,
    IssueResponse,
    KindAddResponse,
    MemoryResponse,
    MutationResponse,
    RecallRequestSchema,
    RecallResponse,
    ScopeSchema,
)


def _scope(value: ScopeSchema) -> MemoryScope:
    return MemoryScope(
        tenant_id=value.tenant_id,
        subject_id=value.subject_id,
        namespace=value.namespace,
    )


def _kinds(values: list[str]) -> frozenset[MemoryKind]:
    return frozenset(MemoryKind(value) for value in values)


def to_add_command(value: AddRequest) -> AddMemoryCommand:
    kinds = (
        _kinds(value.kinds)
        if value.kinds is not None
        else frozenset((MemoryKind.SUMMARY, MemoryKind.FACT, MemoryKind.PROFILE))
    )
    return AddMemoryCommand(
        scope=_scope(value.scope),
        messages=tuple(
            ConversationMessage(
                role=message.role,
                content=message.content,
                timestamp=message.timestamp,
            )
            for message in value.messages
        ),
        idempotency_key=value.idempotency_key,
        event_timestamp=value.event_timestamp,
        kinds=kinds,
        source=value.source,
        session_id=value.session_id,
        locale=value.locale,
        profile_name=value.profile_name,
    )


def from_add_result(value: AddMemoryResult) -> AddResponse:
    return AddResponse(
        status=value.status.value,
        kind_results=[
            KindAddResponse(
                kind=result.kind.value,
                status=result.status.value,
                mutations=[
                    MutationResponse(
                        kind=mutation.kind.value,
                        event=mutation.event.value,
                        status=mutation.status.value,
                        memory_id=mutation.memory_id,
                        issue=(
                            None
                            if mutation.issue is None
                            else IssueResponse(
                                code=mutation.issue.code.value,
                                retryable=mutation.issue.retryable,
                            )
                        ),
                    )
                    for mutation in result.mutations
                ],
                issue=(
                    None
                    if result.issue is None
                    else IssueResponse(
                        code=result.issue.code.value,
                        retryable=result.issue.retryable,
                    )
                ),
            )
            for result in value.kind_results
        ],
    )


def to_recall_request(value: RecallRequestSchema) -> RecallRequest:
    return RecallRequest(
        query=MemoryQuery(
            scope=_scope(value.scope),
            kinds=_kinds(value.kinds),
            source=value.source,
            session_id=value.session_id,
            offset=value.offset,
            limit=value.limit,
        ),
        mode=RecallMode(value.mode),
        semantic_text=value.semantic_text,
        min_score=value.min_score,
    )


def from_recall_result(value: RecallResult) -> RecallResponse:
    page = value.page
    return RecallResponse(
        mode=value.mode.value,
        items=[
            MemoryResponse(
                id=match.record.id,
                scope=ScopeSchema(
                    tenant_id=match.record.scope.tenant_id,
                    subject_id=match.record.scope.subject_id,
                    namespace=match.record.scope.namespace,
                ),
                kind=match.record.kind.value,
                content=match.record.content,
                created_at=match.record.created_at,
                updated_at=match.record.updated_at,
                source=match.record.provenance.source,
                session_id=match.record.provenance.session_id,
                version=match.record.version,
                score=match.score,
            )
            for match in page.items
        ],
        offset=page.offset,
        limit=page.limit,
        next_offset=page.next_offset,
        total_count=page.total_count,
        count_precision=page.count_precision.value,
    )


def to_delete_command(value: DeleteRequest) -> DeleteCommand:
    if isinstance(value, DeleteMemoryRequest):
        return DeleteMemoryCommand(
            scope=_scope(value.scope),
            memory_id=value.memory_id,
            idempotency_key=value.idempotency_key,
            payload_digest=value.payload_digest,
            event_timestamp=value.event_timestamp,
        )
    if isinstance(value, DeleteScopeRequest):
        return DeleteScopeCommand(
            scope=_scope(value.scope),
            kinds=_kinds(value.kinds),
            idempotency_key=value.idempotency_key,
            payload_digest=value.payload_digest,
            event_timestamp=value.event_timestamp,
        )
    if isinstance(value, DeleteSubjectRequest):
        return DeleteSubjectCommand(
            subject=MemorySubject(
                tenant_id=value.subject.tenant_id,
                subject_id=value.subject.subject_id,
            ),
            kinds=_kinds(value.kinds),
            idempotency_key=value.idempotency_key,
            payload_digest=value.payload_digest,
            event_timestamp=value.event_timestamp,
        )
    if isinstance(value, DeleteSessionRequest):
        return DeleteSessionCommand(
            scope=_scope(value.scope),
            session_id=value.session_id,
            idempotency_key=value.idempotency_key,
            payload_digest=value.payload_digest,
            event_timestamp=value.event_timestamp,
        )
    raise AssertionError("unreachable delete request variant")


def from_delete_result(value: DeleteResult) -> DeleteResponse:
    return DeleteResponse(
        matched_count=value.matched_count,
        hard_deleted_count=value.hard_deleted_count,
        contribution_deleted_count=value.contribution_deleted_count,
        tombstoned_count=value.tombstoned_count,
        already_absent_count=value.already_absent_count,
        deleted_count=value.deleted_count,
        barrier_timestamp=value.barrier_timestamp,
        replayed=value.replayed,
    )
