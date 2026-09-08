"""Strict fact-update planning and content-safe persistence attempts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from portable_memory_engine.application.freshness import FreshnessGuard
from portable_memory_engine.application.identity import IdentityStrategy
from portable_memory_engine.application.messages import render_json
from portable_memory_engine.application.model import PromptRunner
from portable_memory_engine.application.models import (
    AddMemoryCommand,
    ExtractionIssue,
    IssueCode,
    MemoryMutation,
    MutationStatus,
)
from portable_memory_engine.domain import (
    ConditionalWrite,
    ConflictError,
    DeleteMemoryCommand,
    DomainValidationError,
    EmbeddingTask,
    IdempotencyConflictError,
    MemoryEvent,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    ProviderError,
    ProviderParseError,
    PutStatus,
    StaleEventError,
    StoreError,
)
from portable_memory_engine.ports import (
    Clock,
    Embedder,
    EmbeddingRequest,
    MemoryStore,
    PromptPurpose,
    PromptRequest,
    PromptTemplate,
)
from portable_memory_engine.prompts import (
    FactOutput,
    OutputSchemaVersion,
    UpdateOutput,
)


@dataclass(frozen=True, slots=True)
class ResolvedUpdateOperation:
    """One parsed operation whose model-facing alias has been resolved."""

    event: MemoryEvent
    memory_id: str | None = field(default=None, repr=False)
    content: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    """Fully validated fact mutation plan plus prompt provenance."""

    operations: tuple[ResolvedUpdateOperation, ...] = field(repr=False)
    model_id: str
    prompt_version: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class WriteAttempt:
    """One persistence attempt, including a structured retry signal."""

    mutation: MemoryMutation
    retry_conflict: bool = False


@dataclass(frozen=True, slots=True)
class ApplyAttempt:
    """Applied prefix and optional terminal/retryable fact mutation."""

    applied: tuple[MemoryMutation, ...]
    failure: MemoryMutation | None = None
    retry_conflict: bool = False


@runtime_checkable
class MemoryUpdateStrategy(Protocol):
    """Replaceable fact planning and persistence strategy used by the engine."""

    async def plan(
        self,
        *,
        facts: FactOutput,
        existing: tuple[MemoryRecord, ...],
        command: AddMemoryCommand,
        override: PromptTemplate | None,
    ) -> UpdatePlan: ...

    async def apply(
        self,
        *,
        plan: UpdatePlan,
        command: AddMemoryCommand,
        input_digest: str,
    ) -> ApplyAttempt: ...

    async def put_content(
        self,
        *,
        command: AddMemoryCommand,
        input_digest: str,
        kind: MemoryKind,
        memory_id: str,
        content: str,
        existing: MemoryRecord | None,
        requested_event: MemoryEvent,
        model_id: str,
        prompt_version: str,
        schema_version: str,
    ) -> WriteAttempt: ...


def _hash_value(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class MemoryUpdater:
    """Default updater that validates aliases and delegates atomic guards to the store."""

    def __init__(
        self,
        *,
        runner: PromptRunner,
        store: MemoryStore,
        embedder: Embedder,
        clock: Clock,
        identity_strategy: IdentityStrategy,
        freshness_guard: FreshnessGuard,
    ) -> None:
        self._runner = runner
        self._store = store
        self._embedder = embedder
        self._clock = clock
        self._identity = identity_strategy
        self._freshness = freshness_guard

    async def plan(
        self,
        *,
        facts: FactOutput,
        existing: tuple[MemoryRecord, ...],
        command: AddMemoryCommand,
        override: PromptTemplate | None,
    ) -> UpdatePlan:
        """Ask for an update plan and reject aliases outside the supplied snapshot."""

        ordered = tuple(sorted(existing, key=lambda record: record.id))
        aliases = {f"memory-{index:04d}": record.id for index, record in enumerate(ordered)}
        existing_payload = [
            {"id": alias, "content": record.content}
            for alias, record in zip(aliases, ordered, strict=True)
        ]
        result = await self._runner.run(
            request=PromptRequest(
                PromptPurpose.UPDATE,
                MemoryKind.FACT,
                locale=command.locale,
                source=command.source,
                override=override,
            ),
            schema_version=OutputSchemaVersion.UPDATE_V1,
            output_type=UpdateOutput,
            variables={
                "existing_memories": render_json(existing_payload),
                "conversation": render_json({"facts": list(facts.facts)}),
            },
        )
        resolved: list[ResolvedUpdateOperation] = []
        target_ids: set[str] = set()
        existing_ids = {record.id for record in ordered}
        for operation in result.output.operations:
            if operation.event is MemoryEvent.ADD:
                if operation.content is None:
                    raise ProviderParseError(
                        schema_version=OutputSchemaVersion.UPDATE_V1.value,
                        reason_code="invalid_action",
                    )
                memory_id = self._identity.fact_id(
                    scope=command.scope,
                    content=operation.content,
                )
            elif operation.event in {MemoryEvent.UPDATE, MemoryEvent.DELETE}:
                if operation.memory_id is None or operation.memory_id not in aliases:
                    raise ProviderParseError(
                        schema_version=OutputSchemaVersion.UPDATE_V1.value,
                        reason_code="invalid_action_target",
                    )
                memory_id = aliases[operation.memory_id]
            else:
                memory_id = None
            if memory_id is not None:
                if memory_id in target_ids:
                    raise ProviderParseError(
                        schema_version=OutputSchemaVersion.UPDATE_V1.value,
                        reason_code="duplicate_action_target",
                    )
                target_ids.add(memory_id)
            if operation.event is MemoryEvent.UPDATE and operation.content is not None:
                canonical_id = self._identity.fact_id(
                    scope=command.scope,
                    content=operation.content,
                )
                if canonical_id != memory_id and canonical_id in existing_ids:
                    raise ConflictError(memory_id=memory_id)
            resolved.append(
                ResolvedUpdateOperation(
                    event=operation.event,
                    memory_id=memory_id,
                    content=operation.content,
                )
            )
        return UpdatePlan(
            operations=tuple(resolved),
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            schema_version=result.schema_version,
        )

    @staticmethod
    def _child_key(command: AddMemoryCommand, *, kind: MemoryKind, memory_id: str) -> str:
        digest = _hash_value(["mutation-key.v1", command.idempotency_key, kind.value, memory_id])
        return f"command-v1-{digest}"

    @staticmethod
    def _payload_digest(
        *,
        input_digest: str,
        memory_id: str,
    ) -> str:
        """Bind a child write to caller input, not state- or model-derived output."""

        return _hash_value(
            [
                "mutation-payload.v1",
                input_digest,
                memory_id,
            ]
        )

    @staticmethod
    def _failure(
        *,
        kind: MemoryKind,
        event: MemoryEvent,
        memory_id: str,
        issue: ExtractionIssue,
        retry_conflict: bool = False,
    ) -> WriteAttempt:
        return WriteAttempt(
            MemoryMutation(
                kind=kind,
                event=event,
                status=MutationStatus.FAILED,
                memory_id=memory_id,
                issue=issue,
            ),
            retry_conflict=retry_conflict,
        )

    async def put_content(
        self,
        *,
        command: AddMemoryCommand,
        input_digest: str,
        kind: MemoryKind,
        memory_id: str,
        content: str,
        existing: MemoryRecord | None,
        requested_event: MemoryEvent,
        model_id: str,
        prompt_version: str,
        schema_version: str,
    ) -> WriteAttempt:
        """Attempt one embedded CAS write; the caller owns conflict recomputation."""

        try:
            await self._freshness.ensure_current(
                scope=command.scope,
                memory_id=memory_id,
                kind=kind,
                event_timestamp=command.event_timestamp,
            )
            embeddings = await self._embedder.embed(
                EmbeddingRequest((content,), EmbeddingTask.DOCUMENT)
            )
            if len(embeddings) != 1:
                raise ProviderError("embedder returned an unexpected result count")
            await self._freshness.ensure_current(
                scope=command.scope,
                memory_id=memory_id,
                kind=kind,
                event_timestamp=command.event_timestamp,
            )
            now = self._clock.now()
            record = MemoryRecord(
                id=memory_id,
                scope=command.scope,
                kind=kind,
                content=content,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                provenance=MemoryProvenance(
                    source=command.source,
                    session_id=command.session_id,
                    event_timestamp=command.event_timestamp,
                    input_digest=input_digest,
                    model_id=model_id,
                    prompt_version=prompt_version,
                    schema_version=schema_version,
                ),
                metadata=command.metadata,
                embedding=embeddings[0],
            )
            result = await self._store.put(
                ConditionalWrite(
                    record=record,
                    idempotency_key=self._child_key(
                        command,
                        kind=kind,
                        memory_id=memory_id,
                    ),
                    payload_digest=self._payload_digest(
                        input_digest=input_digest,
                        memory_id=memory_id,
                    ),
                    event_timestamp=command.event_timestamp,
                    expected_version=existing.version if existing is not None else None,
                )
            )
            if len(result.outcomes) != 1:
                raise DomainValidationError("single memory write returned an invalid outcome")
            outcome = result.outcomes[0]
            event = MemoryEvent.ADD if outcome.status is PutStatus.CREATED else MemoryEvent.UPDATE
            return WriteAttempt(
                MemoryMutation(
                    kind=kind,
                    event=event,
                    status=(
                        MutationStatus.REPLAYED if outcome.replayed else MutationStatus.APPLIED
                    ),
                    memory_id=memory_id,
                )
            )
        except IdempotencyConflictError:
            return self._failure(
                kind=kind,
                event=requested_event,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.IDEMPOTENCY_CONFLICT),
            )
        except StaleEventError:
            return self._failure(
                kind=kind,
                event=requested_event,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.STALE_EVENT),
            )
        except ConflictError:
            return self._failure(
                kind=kind,
                event=requested_event,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.CONFLICT, retryable=True),
                retry_conflict=True,
            )
        except ProviderError:
            return self._failure(
                kind=kind,
                event=requested_event,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.PROVIDER, retryable=True),
            )
        except StoreError:
            return self._failure(
                kind=kind,
                event=requested_event,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.STORE, retryable=True),
            )

    async def _delete(
        self,
        *,
        command: AddMemoryCommand,
        input_digest: str,
        memory_id: str,
    ) -> WriteAttempt:
        try:
            await self._freshness.ensure_current(
                scope=command.scope,
                memory_id=memory_id,
                kind=MemoryKind.FACT,
                event_timestamp=command.event_timestamp,
            )
            result = await self._store.delete(
                DeleteMemoryCommand(
                    scope=command.scope,
                    idempotency_key=self._child_key(
                        command,
                        kind=MemoryKind.FACT,
                        memory_id=memory_id,
                    ),
                    payload_digest=self._payload_digest(
                        input_digest=input_digest,
                        memory_id=memory_id,
                    ),
                    event_timestamp=command.event_timestamp,
                    memory_id=memory_id,
                )
            )
            return WriteAttempt(
                MemoryMutation(
                    kind=MemoryKind.FACT,
                    event=MemoryEvent.DELETE,
                    status=(MutationStatus.REPLAYED if result.replayed else MutationStatus.APPLIED),
                    memory_id=memory_id,
                )
            )
        except IdempotencyConflictError:
            return self._failure(
                kind=MemoryKind.FACT,
                event=MemoryEvent.DELETE,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.IDEMPOTENCY_CONFLICT),
            )
        except StaleEventError:
            return self._failure(
                kind=MemoryKind.FACT,
                event=MemoryEvent.DELETE,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.STALE_EVENT),
            )
        except ConflictError:
            return self._failure(
                kind=MemoryKind.FACT,
                event=MemoryEvent.DELETE,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.CONFLICT, retryable=True),
                retry_conflict=True,
            )
        except StoreError:
            return self._failure(
                kind=MemoryKind.FACT,
                event=MemoryEvent.DELETE,
                memory_id=memory_id,
                issue=ExtractionIssue(IssueCode.STORE, retryable=True),
            )

    async def apply(
        self,
        *,
        plan: UpdatePlan,
        command: AddMemoryCommand,
        input_digest: str,
    ) -> ApplyAttempt:
        """Apply operations in order and stop at the first terminal failure."""

        applied: list[MemoryMutation] = []
        for operation in plan.operations:
            if operation.event is MemoryEvent.NOOP:
                applied.append(
                    MemoryMutation(
                        kind=MemoryKind.FACT,
                        event=MemoryEvent.NOOP,
                        status=MutationStatus.APPLIED,
                    )
                )
                continue
            if operation.memory_id is None:
                raise DomainValidationError("resolved fact mutation requires memory_id")
            existing = await self._store.get(
                scope=command.scope,
                memory_id=operation.memory_id,
            )
            if operation.event in {MemoryEvent.UPDATE, MemoryEvent.DELETE} and existing is None:
                failure = self._failure(
                    kind=MemoryKind.FACT,
                    event=operation.event,
                    memory_id=operation.memory_id,
                    issue=ExtractionIssue(IssueCode.CONFLICT, retryable=True),
                    retry_conflict=True,
                )
            elif operation.event is MemoryEvent.DELETE:
                failure = await self._delete(
                    command=command,
                    input_digest=input_digest,
                    memory_id=operation.memory_id,
                )
            else:
                if operation.content is None:
                    raise DomainValidationError("fact add/update requires content")
                failure = await self.put_content(
                    command=command,
                    input_digest=input_digest,
                    kind=MemoryKind.FACT,
                    memory_id=operation.memory_id,
                    content=operation.content,
                    existing=existing,
                    requested_event=operation.event,
                    model_id=plan.model_id,
                    prompt_version=plan.prompt_version,
                    schema_version=plan.schema_version,
                )
            if failure.mutation.status is MutationStatus.FAILED:
                return ApplyAttempt(
                    tuple(applied),
                    failure=failure.mutation,
                    retry_conflict=failure.retry_conflict,
                )
            applied.append(failure.mutation)
        return ApplyAttempt(tuple(applied))
