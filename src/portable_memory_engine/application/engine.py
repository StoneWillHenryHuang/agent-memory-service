"""Async MemoryEngine add orchestration over replaceable application strategies."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import cast

from portable_memory_engine.application.extraction import (
    FactExtractionStrategy,
    FactExtractor,
    ProfileExtractionStrategy,
    ProfileExtractor,
    SummaryExtractionStrategy,
    SummaryExtractor,
)
from portable_memory_engine.application.freshness import FreshnessGuard
from portable_memory_engine.application.identity import (
    DefaultIdentityStrategy,
    IdentityStrategy,
)
from portable_memory_engine.application.lifecycle import OperationLifecycle
from portable_memory_engine.application.messages import (
    RoleBasedUserMessageFilter,
    UserMessageFilter,
    validate_message_limits,
)
from portable_memory_engine.application.model import PromptRunner
from portable_memory_engine.application.models import (
    AddMemoryCommand,
    AddMemoryResult,
    ExtractionIssue,
    ExtractionLimits,
    IssueCode,
    KindAddResult,
    KindResultStatus,
    MutationStatus,
)
from portable_memory_engine.application.orchestration import KindOrchestrator
from portable_memory_engine.application.support import (
    AllowAllAccessPolicy,
    NoopObserver,
    SystemClock,
)
from portable_memory_engine.application.update import MemoryUpdater, MemoryUpdateStrategy
from portable_memory_engine.domain import (
    AccessDeniedError,
    CapabilityError,
    ConflictError,
    DomainValidationError,
    FrozenJsonObject,
    IdempotencyConflictError,
    JsonValue,
    MemoryKind,
    ProviderError,
    ProviderParseError,
    StaleEventError,
    StoreError,
)
from portable_memory_engine.ports import (
    MANDATORY_WRITE_CAPABILITIES,
    AccessOperation,
    AccessPolicy,
    AccessRequest,
    AsyncLifecycle,
    ChatModel,
    Clock,
    Embedder,
    MemoryStore,
    ObservationEvent,
    ObservationOutcome,
    Observer,
    PromptProvider,
)

_KIND_ORDER = (MemoryKind.SUMMARY, MemoryKind.FACT, MemoryKind.PROFILE)


def _plain_json(value: JsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _input_digest(command: AddMemoryCommand) -> str:
    overrides = command.prompt_overrides
    override_values = []
    for name in ("summary", "fact", "profile", "update"):
        prompt = getattr(overrides, name)
        override_values.append(
            None
            if prompt is None
            else [
                name,
                prompt.identifier,
                prompt.version,
                prompt.response_schema_version,
            ]
        )
    value = {
        "version": "add-input.v1",
        "scope": [
            command.scope.tenant_id,
            command.scope.subject_id,
            command.scope.namespace,
        ],
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "timestamp": (
                    message.timestamp.isoformat() if message.timestamp is not None else None
                ),
                "metadata": {key: _plain_json(item) for key, item in message.metadata.items()},
            }
            for message in command.messages
        ],
        "event_timestamp": command.event_timestamp.isoformat(),
        "kinds": sorted(kind.value for kind in command.kinds),
        "source": command.source,
        "session_id": command.session_id,
        "locale": command.locale,
        "profile_name": command.profile_name,
        "metadata": {key: _plain_json(item) for key, item in command.metadata.items()},
        "prompt_overrides": override_values,
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class MemoryEngine:
    """Small async application service orchestrating extraction and safe writes."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        chat_model: ChatModel,
        embedder: Embedder,
        prompt_provider: PromptProvider,
        access_policy: AccessPolicy | None = None,
        observer: Observer | None = None,
        clock: Clock | None = None,
        identity_strategy: IdentityStrategy | None = None,
        user_message_filter: UserMessageFilter | None = None,
        summary_extractor: SummaryExtractionStrategy | None = None,
        fact_extractor: FactExtractionStrategy | None = None,
        profile_extractor: ProfileExtractionStrategy | None = None,
        memory_updater: MemoryUpdateStrategy | None = None,
        limits: ExtractionLimits | None = None,
        owns_resources: bool = True,
    ) -> None:
        limits = limits or ExtractionLimits()
        if not isinstance(limits, ExtractionLimits):
            raise DomainValidationError("limits must be ExtractionLimits")
        if not isinstance(owns_resources, bool):
            raise DomainValidationError("owns_resources must be boolean")
        store.capabilities.require(
            operation="memory.add",
            capabilities=MANDATORY_WRITE_CAPABILITIES,
        )
        self._access_policy = access_policy or AllowAllAccessPolicy()
        default_observer = observer is None
        self._observer = observer or NoopObserver()
        self._clock = clock or SystemClock()
        self._identity = identity_strategy or DefaultIdentityStrategy()
        self._limits = limits
        freshness_guard = FreshnessGuard(store)
        runner = PromptRunner(
            chat_model=chat_model,
            prompt_provider=prompt_provider,
            limits=limits,
        )
        message_filter = user_message_filter or RoleBasedUserMessageFilter()
        selected_summary_extractor = summary_extractor or SummaryExtractor(runner)
        selected_fact_extractor = fact_extractor or FactExtractor(runner, message_filter)
        selected_profile_extractor = profile_extractor or ProfileExtractor(runner)
        selected_updater = memory_updater or MemoryUpdater(
            runner=runner,
            store=store,
            embedder=embedder,
            clock=self._clock,
            identity_strategy=self._identity,
            freshness_guard=freshness_guard,
        )
        resources = cast(
            "tuple[AsyncLifecycle, ...]",
            (store, chat_model, embedder, prompt_provider, self._observer),
        )
        unique: list[AsyncLifecycle] = []
        seen: set[int] = set()
        for resource in resources:
            if id(resource) not in seen:
                seen.add(id(resource))
                unique.append(resource)
        managed_resources = (
            tuple(unique)
            if owns_resources
            else (cast("AsyncLifecycle", self._observer),)
            if default_observer
            else ()
        )
        self._lifecycle = OperationLifecycle(managed_resources)
        self._kind_orchestrator = KindOrchestrator(
            store=store,
            identity_strategy=self._identity,
            freshness_guard=freshness_guard,
            summary_extractor=selected_summary_extractor,
            fact_extractor=selected_fact_extractor,
            profile_extractor=selected_profile_extractor,
            memory_updater=selected_updater,
            limits=self._limits,
            conflict_retry_observer=self._emit_conflict_retry,
        )

    async def __aenter__(self) -> MemoryEngine:
        """Open owned resources and return the engine."""

        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        """Close owned resources after in-flight add operations complete."""

        await self.close()

    async def open(self) -> None:
        """Open owned lifecycle dependencies."""

        await self._lifecycle.open()

    async def close(self) -> None:
        """Wait for in-flight work and close owned dependencies."""

        await self._lifecycle.close()

    async def _begin_operation(self) -> None:
        await self._lifecycle.begin_operation()

    async def _end_operation(self) -> None:
        await self._lifecycle.end_operation()

    async def _emit(
        self,
        *,
        operation: str,
        outcome: ObservationOutcome,
        duration_seconds: float,
        kind: MemoryKind | None = None,
        item_count: int | None = None,
    ) -> None:
        await self._observer.emit(
            ObservationEvent(
                operation=operation,
                outcome=outcome,
                occurred_at=self._clock.now(),
                duration_seconds=duration_seconds,
                kind=kind,
                item_count=item_count,
            )
        )

    async def _emit_conflict_retry(self, kind: MemoryKind, attempt: int) -> None:
        await self._emit(
            operation="memory.add.conflict_retry",
            outcome=ObservationOutcome.REJECTED,
            duration_seconds=0.0,
            kind=kind,
            item_count=attempt,
        )

    @staticmethod
    def _issue_result(
        *,
        kind: MemoryKind,
        status: KindResultStatus,
        issue: ExtractionIssue,
    ) -> KindAddResult:
        return KindAddResult(kind=kind, status=status, issue=issue)

    @classmethod
    def _expected_failure(cls, kind: MemoryKind, error: Exception) -> KindAddResult:
        if isinstance(error, IdempotencyConflictError):
            return cls._issue_result(
                kind=kind,
                status=KindResultStatus.CONFLICT,
                issue=ExtractionIssue(IssueCode.IDEMPOTENCY_CONFLICT),
            )
        if isinstance(error, StaleEventError):
            return cls._issue_result(
                kind=kind,
                status=KindResultStatus.STALE,
                issue=ExtractionIssue(IssueCode.STALE_EVENT),
            )
        if isinstance(error, ConflictError):
            return cls._issue_result(
                kind=kind,
                status=KindResultStatus.CONFLICT,
                issue=ExtractionIssue(IssueCode.CONFLICT, retryable=True),
            )
        if isinstance(error, ProviderParseError):
            return cls._issue_result(
                kind=kind,
                status=KindResultStatus.FAILED,
                issue=ExtractionIssue(IssueCode.PROVIDER_PARSE),
            )
        if isinstance(error, ProviderError):
            return cls._issue_result(
                kind=kind,
                status=KindResultStatus.FAILED,
                issue=ExtractionIssue(IssueCode.PROVIDER, retryable=True),
            )
        if isinstance(error, StoreError):
            return cls._issue_result(
                kind=kind,
                status=KindResultStatus.FAILED,
                issue=ExtractionIssue(IssueCode.STORE, retryable=True),
            )
        if isinstance(error, DomainValidationError):
            return cls._issue_result(
                kind=kind,
                status=KindResultStatus.FAILED,
                issue=ExtractionIssue(IssueCode.VALIDATION),
            )
        raise error

    async def _run_kind(
        self,
        kind: MemoryKind,
        operation: Callable[[], Awaitable[KindAddResult]],
    ) -> KindAddResult:
        started = time.perf_counter()
        try:
            result = await operation()
        except (
            ConflictError,
            ProviderError,
            StoreError,
            DomainValidationError,
        ) as error:
            result = self._expected_failure(kind, error)
        outcome = (
            ObservationOutcome.SUCCEEDED
            if result.accepted
            else ObservationOutcome.REJECTED
            if result.status in {KindResultStatus.STALE, KindResultStatus.CONFLICT}
            else ObservationOutcome.FAILED
        )
        await self._emit(
            operation="memory.add.kind",
            outcome=outcome,
            duration_seconds=time.perf_counter() - started,
            kind=kind,
            item_count=sum(
                mutation.status is not MutationStatus.FAILED for mutation in result.mutations
            ),
        )
        return result

    async def add(self, command: AddMemoryCommand) -> AddMemoryResult:
        """Authorize, extract requested kinds, persist safely, and return partial results."""

        if not isinstance(command, AddMemoryCommand):
            raise DomainValidationError("memory add requires AddMemoryCommand")
        await self._begin_operation()
        started = time.perf_counter()
        try:
            validate_message_limits(command.messages, self._limits)
            unsupported = command.kinds.difference(_KIND_ORDER)
            if unsupported:
                missing = min(unsupported, key=lambda kind: kind.value)
                raise CapabilityError(
                    operation="memory.add",
                    capability=f"extractor:{missing.value}",
                )
            decision = await self._access_policy.authorize(
                AccessRequest(command.scope, AccessOperation.WRITE)
            )
            if not decision.allowed:
                raise AccessDeniedError(operation="memory.add")
            digest = _input_digest(command)
            kind_results: list[KindAddResult] = []
            for kind in _KIND_ORDER:
                if kind not in command.kinds:
                    continue
                if kind == MemoryKind.SUMMARY:
                    if command.session_id is None:
                        raise DomainValidationError("summary extraction requires session_id")
                    memory_id = self._identity.summary_id(
                        scope=command.scope,
                        session_id=command.session_id,
                    )
                    result = await self._run_kind(
                        kind,
                        partial(
                            self._kind_orchestrator.singleton,
                            command=command,
                            input_digest=digest,
                            kind=MemoryKind.SUMMARY,
                            memory_id=memory_id,
                        ),
                    )
                elif kind == MemoryKind.FACT:
                    result = await self._run_kind(
                        kind,
                        lambda: self._kind_orchestrator.facts(
                            command=command,
                            input_digest=digest,
                        ),
                    )
                else:
                    memory_id = self._identity.profile_id(
                        scope=command.scope,
                        profile_name=command.profile_name,
                    )
                    result = await self._run_kind(
                        kind,
                        partial(
                            self._kind_orchestrator.singleton,
                            command=command,
                            input_digest=digest,
                            kind=MemoryKind.PROFILE,
                            memory_id=memory_id,
                        ),
                    )
                kind_results.append(result)
            aggregate = AddMemoryResult.from_kind_results(
                scope=command.scope,
                idempotency_key=command.idempotency_key,
                kind_results=kind_results,
            )
            await self._emit(
                operation="memory.add",
                outcome=(
                    ObservationOutcome.SUCCEEDED
                    if aggregate.status.value == "succeeded"
                    else ObservationOutcome.FAILED
                ),
                duration_seconds=time.perf_counter() - started,
                item_count=sum(
                    mutation.status is not MutationStatus.FAILED
                    for result in kind_results
                    for mutation in result.mutations
                ),
            )
            return aggregate
        except AccessDeniedError:
            await self._emit(
                operation="memory.add",
                outcome=ObservationOutcome.REJECTED,
                duration_seconds=time.perf_counter() - started,
                item_count=0,
            )
            raise
        except Exception:
            await self._emit(
                operation="memory.add",
                outcome=ObservationOutcome.FAILED,
                duration_seconds=time.perf_counter() - started,
                item_count=0,
            )
            raise
        finally:
            await self._end_operation()
