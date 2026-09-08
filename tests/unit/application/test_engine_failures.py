"""Conflict, stale, parser, partial-result, and fail-fast engine tests."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from portable_memory_engine.adapters import DeterministicEmbedder, InMemoryMemoryStore
from portable_memory_engine.application import (
    AddResultStatus,
    DefaultIdentityStrategy,
    ExtractionLimits,
    IssueCode,
    KindResultStatus,
    MemoryEngine,
    MutationStatus,
)
from portable_memory_engine.domain import (
    AccessDeniedError,
    CapabilityError,
    ConditionalWrite,
    ConflictError,
    ConversationMessage,
    DomainValidationError,
    MemoryKind,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    ProviderUnavailableError,
    PutResult,
)
from portable_memory_engine.ports import ChatRequest, StorageCapabilities
from portable_memory_engine.prompts import DefaultPromptProvider
from tests.unit.application._helpers import (
    PROCESSING_TIME,
    DenyAccessPolicy,
    RecordingChatModel,
    build_engine,
    command,
    response,
)


class ConflictStore(InMemoryMemoryStore):
    """Synthetic store injecting a fixed number of CAS conflicts."""

    def __init__(self, conflicts: int) -> None:
        super().__init__()
        self._remaining_conflicts = conflicts
        self.put_calls = 0

    async def put(self, command: ConditionalWrite) -> PutResult:
        self.put_calls += 1
        if self._remaining_conflicts:
            self._remaining_conflicts -= 1
            raise ConflictError(memory_id=command.record.id)
        return await super().put(command)


class SecondPutConflictStore(InMemoryMemoryStore):
    """Store that commits one fact and conflicts on the next fact."""

    def __init__(self) -> None:
        super().__init__()
        self.put_calls = 0

    async def put(self, command: ConditionalWrite) -> PutResult:
        self.put_calls += 1
        if self.put_calls == 2:
            raise ConflictError(memory_id=command.record.id)
        return await super().put(command)


def test_one_occ_conflict_recomputes_then_succeeds() -> None:
    async def scenario() -> None:
        store = ConflictStore(1)
        payload = response('{"schema_version":"summary.v1","summary":"Recomputed summary"}')
        engine, _, model, observer, _ = build_engine((payload, payload), store=store)
        async with engine:
            result = await engine.add(
                command(kinds=frozenset({MemoryKind.SUMMARY}), key="retry-once")
            )

        assert result.status is AddResultStatus.SUCCEEDED
        assert store.put_calls == 2
        assert model.call_count == 2
        assert sum(event.operation == "memory.add.conflict_retry" for event in observer.events) == 1

    asyncio.run(scenario())


def test_two_occ_conflicts_exhaust_the_bounded_retry() -> None:
    async def scenario() -> None:
        store = ConflictStore(2)
        payload = response('{"schema_version":"summary.v1","summary":"Conflicting summary"}')
        engine, _, model, _, _ = build_engine((payload, payload), store=store)
        async with engine:
            result = await engine.add(
                command(kinds=frozenset({MemoryKind.SUMMARY}), key="retry-twice")
            )

        kind = result.kind_results[0]
        assert result.status is AddResultStatus.FAILED
        assert kind.status is KindResultStatus.CONFLICT
        assert kind.issue is not None and kind.issue.code is IssueCode.CONFLICT
        assert kind.mutations[0].status is MutationStatus.FAILED
        assert store.put_calls == 2
        assert model.call_count == 2

    asyncio.run(scenario())


def test_newer_write_during_llm_causes_pre_write_stale_result() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        value = command(kinds=frozenset({MemoryKind.SUMMARY}), key="stale-command")
        memory_id = DefaultIdentityStrategy().summary_id(
            scope=value.scope,
            session_id=value.session_id or "missing",
        )

        async def commit_newer(_request: ChatRequest, _index: int) -> None:
            newer_time = value.event_timestamp + timedelta(minutes=1)
            await store.put(
                ConditionalWrite(
                    record=MemoryRecord(
                        id=memory_id,
                        scope=value.scope,
                        kind=MemoryKind.SUMMARY,
                        content="Newer synthetic summary",
                        created_at=PROCESSING_TIME,
                        updated_at=PROCESSING_TIME,
                        provenance=MemoryProvenance(event_timestamp=newer_time),
                    ),
                    idempotency_key="newer-summary-command",
                    payload_digest="newer-summary-digest",
                    event_timestamp=newer_time,
                )
            )

        model = RecordingChatModel(
            (response('{"schema_version":"summary.v1","summary":"Older synthetic summary"}'),),
            hook=commit_newer,
        )
        engine, _, _, _, _ = build_engine((), store=store, model=model)
        async with engine:
            result = await engine.add(value)
            stored = await store.get(scope=value.scope, memory_id=memory_id)

        assert result.kind_results[0].status is KindResultStatus.STALE
        assert result.kind_results[0].issue is not None
        assert result.kind_results[0].issue.code is IssueCode.STALE_EVENT
        assert stored is not None and stored.content == "Newer synthetic summary"

    asyncio.run(scenario())


def test_older_fact_command_is_rejected_before_model_work() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        value = command(
            kinds=frozenset({MemoryKind.FACT}),
            key="stale-fact-command",
        )
        fact_id = DefaultIdentityStrategy().fact_id(
            scope=value.scope,
            content="Newer synthetic fact",
        )
        newer_time = value.event_timestamp + timedelta(minutes=1)
        engine, _, model, _, _ = build_engine((), store=store)
        async with engine:
            await store.put(
                ConditionalWrite(
                    record=MemoryRecord(
                        id=fact_id,
                        scope=value.scope,
                        kind=MemoryKind.FACT,
                        content="Newer synthetic fact",
                        created_at=PROCESSING_TIME,
                        updated_at=PROCESSING_TIME,
                        provenance=MemoryProvenance(event_timestamp=newer_time),
                    ),
                    idempotency_key="newer-fact-command",
                    payload_digest="newer-fact-digest",
                    event_timestamp=newer_time,
                )
            )
            result = await engine.add(value)

        assert result.kind_results[0].status is KindResultStatus.STALE
        assert model.call_count == 0

    asyncio.run(scenario())


def test_empty_response_and_invalid_action_are_structured_parse_failures() -> None:
    async def scenario() -> None:
        summary_engine, summary_store, _, _, _ = build_engine((response(""),))
        async with summary_engine:
            empty = await summary_engine.add(
                command(kinds=frozenset({MemoryKind.SUMMARY}), key="empty-summary")
            )
            page = await summary_store.query(MemoryQuery(scope=empty.scope))

        invalid_engine, invalid_store, _, _, _ = build_engine(
            (
                response('{"schema_version":"fact.v1","facts":["Synthetic fact"]}'),
                response(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"delete","memory_id":"fabricated-alias",'
                    '"content":null}]}'
                ),
            )
        )
        async with invalid_engine:
            invalid = await invalid_engine.add(
                command(kinds=frozenset({MemoryKind.FACT}), key="invalid-action")
            )
            invalid_page = await invalid_store.query(MemoryQuery(scope=invalid.scope))

        assert empty.kind_results[0].issue is not None
        assert empty.kind_results[0].issue.code is IssueCode.PROVIDER_PARSE
        assert page.items == ()
        assert invalid.kind_results[0].issue is not None
        assert invalid.kind_results[0].issue.code is IssueCode.PROVIDER_PARSE
        assert invalid_page.items == ()

    asyncio.run(scenario())


def test_failure_in_one_kind_returns_partial_result_and_continues() -> None:
    async def scenario() -> None:
        engine, store, _, _, _ = build_engine(
            (
                response('{"schema_version":"summary.v1","summary":"Valid summary"}'),
                response(""),
                response('{"schema_version":"profile.v1","profile":"Valid profile"}'),
            )
        )
        async with engine:
            result = await engine.add(command(key="partial-command"))
            page = await store.query(MemoryQuery(scope=result.scope, limit=10))

        assert result.status is AddResultStatus.PARTIAL
        assert [item.status for item in result.kind_results] == [
            KindResultStatus.SUCCEEDED,
            KindResultStatus.FAILED,
            KindResultStatus.SUCCEEDED,
        ]
        assert {item.record.kind for item in page.items} == {
            MemoryKind.SUMMARY,
            MemoryKind.PROFILE,
        }

    asyncio.run(scenario())


def test_fact_action_prefix_is_reported_when_later_action_conflicts() -> None:
    async def scenario() -> None:
        store = SecondPutConflictStore()
        engine, _, _, _, _ = build_engine(
            (
                response(
                    '{"schema_version":"fact.v1",'
                    '"facts":["First synthetic fact","Second synthetic fact"]}'
                ),
                response(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"add","memory_id":null,'
                    '"content":"First synthetic fact"},'
                    '{"event":"add","memory_id":null,'
                    '"content":"Second synthetic fact"}]}'
                ),
            ),
            store=store,
            limits=ExtractionLimits(max_conflict_retries=0),
        )
        async with engine:
            result = await engine.add(
                command(kinds=frozenset({MemoryKind.FACT}), key="partial-fact")
            )

        kind = result.kind_results[0]
        assert result.status is AddResultStatus.PARTIAL
        assert kind.status is KindResultStatus.PARTIAL
        assert [mutation.status for mutation in kind.mutations] == [
            MutationStatus.APPLIED,
            MutationStatus.FAILED,
        ]

    asyncio.run(scenario())


def test_non_user_messages_skip_fact_model_call() -> None:
    async def scenario() -> None:
        engine, _, model, _, _ = build_engine(())
        value = command(
            kinds=frozenset({MemoryKind.FACT}),
            key="assistant-only",
            messages=(
                ConversationMessage(role="assistant", content="Synthetic response"),
                ConversationMessage(role="tool", content="Synthetic tool result"),
            ),
        )
        async with engine:
            result = await engine.add(value)

        assert result.kind_results[0].status is KindResultStatus.NO_CHANGE
        assert model.call_count == 0

    asyncio.run(scenario())


def test_size_limit_and_access_denial_fail_before_model_work() -> None:
    async def scenario() -> None:
        limited_engine, _, limited_model, _, _ = build_engine(
            (),
            limits=ExtractionLimits(max_message_characters=5),
        )
        with pytest.raises(DomainValidationError, match="content exceeds"):
            async with limited_engine:
                await limited_engine.add(
                    command(
                        kinds=frozenset({MemoryKind.FACT}),
                        messages=(ConversationMessage("user", "too long"),),
                    )
                )
        denied_engine, _, denied_model, _, _ = build_engine(
            (),
            access_policy=DenyAccessPolicy(),
        )
        with pytest.raises(AccessDeniedError):
            async with denied_engine:
                await denied_engine.add(command(kinds=frozenset({MemoryKind.FACT}), key="denied"))

        assert limited_model.call_count == 0
        assert denied_model.call_count == 0

    asyncio.run(scenario())


def test_provider_failure_is_sanitized_in_kind_result() -> None:
    async def scenario() -> None:
        engine, _, _, _, _ = build_engine(
            (ProviderUnavailableError("synthetic provider unavailable"),)
        )
        async with engine:
            result = await engine.add(
                command(kinds=frozenset({MemoryKind.SUMMARY}), key="provider-failure")
            )

        assert result.kind_results[0].status is KindResultStatus.FAILED
        assert result.kind_results[0].issue is not None
        assert result.kind_results[0].issue.code is IssueCode.PROVIDER
        assert "synthetic provider unavailable" not in repr(result)

    asyncio.run(scenario())


def test_missing_write_capabilities_fail_at_construction() -> None:
    class IncapableStore(InMemoryMemoryStore):
        @property
        def capabilities(self) -> StorageCapabilities:
            return StorageCapabilities()

    model = RecordingChatModel(())
    with pytest.raises(CapabilityError, match="atomic_compare_and_swap"):
        MemoryEngine(
            store=IncapableStore(),
            chat_model=model,
            embedder=DeterministicEmbedder(),
            prompt_provider=DefaultPromptProvider(),
        )
