"""Per-kind extraction orchestration kept separate from the public engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from portable_memory_engine.application.extraction import (
    ExtractionContext,
    FactExtractionStrategy,
    ProfileExtractionStrategy,
    SummaryExtractionStrategy,
)
from portable_memory_engine.application.freshness import FreshnessGuard
from portable_memory_engine.application.identity import IdentityStrategy
from portable_memory_engine.application.models import (
    AddMemoryCommand,
    ExtractionLimits,
    IssueCode,
    KindAddResult,
    KindResultStatus,
    MemoryMutation,
    MutationStatus,
)
from portable_memory_engine.application.update import (
    ApplyAttempt,
    MemoryUpdateStrategy,
)
from portable_memory_engine.domain import (
    DomainValidationError,
    MemoryEvent,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
)
from portable_memory_engine.ports import MemoryStore

ConflictRetryObserver = Callable[[MemoryKind, int], Awaitable[None]]


class KindOrchestrator:
    """Coordinate singleton and multi-valued kind strategies with safe retries."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        identity_strategy: IdentityStrategy,
        freshness_guard: FreshnessGuard,
        summary_extractor: SummaryExtractionStrategy,
        fact_extractor: FactExtractionStrategy,
        profile_extractor: ProfileExtractionStrategy,
        memory_updater: MemoryUpdateStrategy,
        limits: ExtractionLimits,
        conflict_retry_observer: ConflictRetryObserver,
    ) -> None:
        self._store = store
        self._identity = identity_strategy
        self._freshness = freshness_guard
        self._summary_extractor = summary_extractor
        self._fact_extractor = fact_extractor
        self._profile_extractor = profile_extractor
        self._updater = memory_updater
        self._limits = limits
        self._observe_conflict_retry = conflict_retry_observer

    def _check_existing_content(self, records: tuple[MemoryRecord, ...]) -> None:
        if sum(len(record.content) for record in records) > (
            self._limits.max_existing_content_characters
        ):
            raise DomainValidationError("existing memory content exceeds the extraction limit")

    async def singleton(
        self,
        *,
        command: AddMemoryCommand,
        input_digest: str,
        kind: MemoryKind,
        memory_id: str,
    ) -> KindAddResult:
        """Extract and persist one summary or profile logical target."""

        for attempt in range(self._limits.max_conflict_retries + 1):
            await self._freshness.ensure_current(
                scope=command.scope,
                memory_id=memory_id,
                kind=kind,
                event_timestamp=command.event_timestamp,
            )
            existing = await self._store.get(scope=command.scope, memory_id=memory_id)
            if existing is not None and existing.kind != kind:
                raise DomainValidationError("deterministic identity contains another memory kind")
            if existing is not None:
                self._check_existing_content((existing,))
            context = ExtractionContext(
                messages=tuple(command.messages),
                existing=existing,
                locale=command.locale,
                source=command.source,
                override=(
                    command.prompt_overrides.summary
                    if kind == MemoryKind.SUMMARY
                    else command.prompt_overrides.profile
                ),
            )
            if kind == MemoryKind.SUMMARY:
                summary_artifact = await self._summary_extractor.extract(context)
                content = summary_artifact.output.summary
                model_id = summary_artifact.model_id
                prompt_version = summary_artifact.prompt_version
                schema_version = summary_artifact.schema_version
            else:
                profile_artifact = await self._profile_extractor.extract(context)
                if profile_artifact.output.profile is None:
                    return KindAddResult(kind, KindResultStatus.NO_CHANGE)
                content = profile_artifact.output.profile
                model_id = profile_artifact.model_id
                prompt_version = profile_artifact.prompt_version
                schema_version = profile_artifact.schema_version
            await self._freshness.ensure_current(
                scope=command.scope,
                memory_id=memory_id,
                kind=kind,
                event_timestamp=command.event_timestamp,
            )
            write = await self._updater.put_content(
                command=command,
                input_digest=input_digest,
                kind=kind,
                memory_id=memory_id,
                content=content,
                existing=existing,
                requested_event=(MemoryEvent.ADD if existing is None else MemoryEvent.UPDATE),
                model_id=model_id,
                prompt_version=prompt_version,
                schema_version=schema_version,
            )
            if write.retry_conflict and attempt < self._limits.max_conflict_retries:
                await self._observe_conflict_retry(kind, attempt + 1)
                continue
            if write.mutation.status is not MutationStatus.FAILED:
                return KindAddResult(kind, KindResultStatus.SUCCEEDED, (write.mutation,))
            issue = write.mutation.issue
            if issue is None:
                raise DomainValidationError("failed singleton mutation has no issue")
            status = (
                KindResultStatus.STALE
                if issue.code is IssueCode.STALE_EVENT
                else KindResultStatus.CONFLICT
                if issue.code in {IssueCode.CONFLICT, IssueCode.IDEMPOTENCY_CONFLICT}
                else KindResultStatus.FAILED
            )
            return KindAddResult(kind, status, (write.mutation,), issue)
        raise DomainValidationError("singleton retry loop terminated unexpectedly")

    async def _load_facts(self, command: AddMemoryCommand) -> tuple[MemoryRecord, ...]:
        page = await self._store.query(
            MemoryQuery(
                scope=command.scope,
                kinds=frozenset({MemoryKind.FACT}),
                limit=self._limits.max_existing_memories,
            )
        )
        if page.next_offset is not None or (
            page.total_count is not None and page.total_count > self._limits.max_existing_memories
        ):
            raise DomainValidationError("existing fact count exceeds the extraction limit")
        records = tuple(match.record for match in page.items)
        self._check_existing_content(records)
        return records

    @staticmethod
    def _merge_mutations(
        existing: list[MemoryMutation], additions: tuple[MemoryMutation, ...]
    ) -> None:
        keys = {(mutation.event, mutation.memory_id) for mutation in existing}
        for mutation in additions:
            key = (mutation.event, mutation.memory_id)
            if key not in keys:
                keys.add(key)
                existing.append(mutation)

    @staticmethod
    def _fact_terminal(
        *,
        mutations: list[MemoryMutation],
        failure: MemoryMutation,
    ) -> KindAddResult:
        issue = failure.issue
        if issue is None:
            raise DomainValidationError("failed fact mutation has no issue")
        successful = any(
            mutation.status is not MutationStatus.FAILED and mutation.event is not MemoryEvent.NOOP
            for mutation in mutations
        )
        status = (
            KindResultStatus.PARTIAL
            if successful
            else KindResultStatus.STALE
            if issue.code is IssueCode.STALE_EVENT
            else KindResultStatus.CONFLICT
            if issue.code in {IssueCode.CONFLICT, IssueCode.IDEMPOTENCY_CONFLICT}
            else KindResultStatus.FAILED
        )
        return KindAddResult(
            MemoryKind.FACT,
            status,
            (*mutations, failure),
            issue,
        )

    async def facts(
        self,
        *,
        command: AddMemoryCommand,
        input_digest: str,
    ) -> KindAddResult:
        """Extract candidate facts, validate actions, and persist an ordered prefix."""

        await self._freshness.ensure_current(
            scope=command.scope,
            memory_id=self._identity.fact_guard_id(scope=command.scope),
            kind=MemoryKind.FACT,
            event_timestamp=command.event_timestamp,
        )
        initial_existing = await self._load_facts(command)
        await self._freshness.ensure_many_current(
            scope=command.scope,
            targets=((record.id, MemoryKind.FACT) for record in initial_existing),
            event_timestamp=command.event_timestamp,
        )
        artifact = await self._fact_extractor.extract(
            ExtractionContext(
                messages=tuple(command.messages),
                locale=command.locale,
                source=command.source,
                override=command.prompt_overrides.fact,
            )
        )
        if artifact is None or not artifact.output.facts:
            return KindAddResult(MemoryKind.FACT, KindResultStatus.NO_CHANGE)
        candidate_targets = tuple(
            (
                self._identity.fact_id(scope=command.scope, content=fact),
                MemoryKind.FACT,
            )
            for fact in artifact.output.facts
        )
        await self._freshness.ensure_many_current(
            scope=command.scope,
            targets=candidate_targets,
            event_timestamp=command.event_timestamp,
        )
        mutations: list[MemoryMutation] = []
        for attempt in range(self._limits.max_conflict_retries + 1):
            existing = initial_existing if attempt == 0 else await self._load_facts(command)
            await self._freshness.ensure_many_current(
                scope=command.scope,
                targets=(
                    *candidate_targets,
                    *((record.id, MemoryKind.FACT) for record in existing),
                ),
                event_timestamp=command.event_timestamp,
            )
            plan = await self._updater.plan(
                facts=artifact.output,
                existing=existing,
                command=command,
                override=command.prompt_overrides.update,
            )
            applied: ApplyAttempt = await self._updater.apply(
                plan=plan,
                command=command,
                input_digest=input_digest,
            )
            self._merge_mutations(mutations, applied.applied)
            if applied.failure is None:
                substantive = any(mutation.event is not MemoryEvent.NOOP for mutation in mutations)
                return KindAddResult(
                    MemoryKind.FACT,
                    (KindResultStatus.SUCCEEDED if substantive else KindResultStatus.NO_CHANGE),
                    tuple(mutations),
                )
            if applied.retry_conflict and attempt < self._limits.max_conflict_retries:
                await self._observe_conflict_retry(MemoryKind.FACT, attempt + 1)
                continue
            return self._fact_terminal(mutations=mutations, failure=applied.failure)
        raise DomainValidationError("fact retry loop terminated unexpectedly")
