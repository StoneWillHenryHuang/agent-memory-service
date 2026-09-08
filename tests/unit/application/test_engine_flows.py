"""Synthetic end-to-end extraction and mutation flows over fake providers."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from portable_memory_engine.application import (
    AddResultStatus,
    IssueCode,
    KindResultStatus,
    MutationStatus,
)
from portable_memory_engine.domain import (
    ConversationMessage,
    MemoryEvent,
    MemoryKind,
    MemoryQuery,
    PutStatus,
)
from tests.unit.application._helpers import build_engine, command, response


def test_summary_fact_profile_full_flow_with_content_free_observation() -> None:
    async def scenario() -> None:
        engine, store, model, observer, _ = build_engine(
            (
                response(
                    '{"schema_version":"summary.v1","summary":"Synthetic trip planning summary"}'
                ),
                response('{"schema_version":"fact.v1","facts":["Prefers morning trains"]}'),
                response(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"add","memory_id":null,'
                    '"content":"Prefers morning trains"}]}'
                ),
                response('{"schema_version":"profile.v1","profile":"Prefers early-day travel"}'),
            )
        )
        async with engine:
            result = await engine.add(command())
            page = await store.query(MemoryQuery(scope=result.scope, limit=10))

        assert result.status is AddResultStatus.SUCCEEDED
        assert [item.status for item in result.kind_results] == [
            KindResultStatus.SUCCEEDED,
            KindResultStatus.SUCCEEDED,
            KindResultStatus.SUCCEEDED,
        ]
        assert {item.record.kind for item in page.items} == {
            MemoryKind.SUMMARY,
            MemoryKind.FACT,
            MemoryKind.PROFILE,
        }
        assert all(item.record.provenance.input_digest for item in page.items)
        assert all(item.record.provenance.prompt_version == "prompt.v1" for item in page.items)
        assert model.call_count == 4
        assert observer.is_open is False
        assert {event.operation for event in observer.events} == {
            "memory.add.kind",
            "memory.add",
        }
        assert all(not hasattr(event, "content") for event in observer.events)
        assert all(not hasattr(event, "messages") for event in observer.events)

        fact_request = model.requests[1]
        rendered_fact_input = fact_request.messages[1].content
        assert "I prefer morning trains" in rendered_fact_input
        assert "Synthetic assistant context" not in rendered_fact_input
        assert "I prefer morning trains" not in repr(fact_request)

    asyncio.run(scenario())


def test_fact_add_update_delete_and_noop_preserve_target_identity() -> None:
    async def scenario() -> None:
        engine, store, _, _, _ = build_engine(
            (
                response('{"schema_version":"fact.v1","facts":["Uses rail"]}'),
                response(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"add","memory_id":null,"content":"Uses rail"}]}'
                ),
                response('{"schema_version":"fact.v1","facts":["Uses buses"]}'),
                response(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"update","memory_id":"memory-0000",'
                    '"content":"Uses buses"}]}'
                ),
                response('{"schema_version":"fact.v1","facts":["No longer relevant"]}'),
                response(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"delete","memory_id":"memory-0000","content":null}]}'
                ),
                response('{"schema_version":"fact.v1","facts":["Nothing durable"]}'),
                response(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"noop","memory_id":null,"content":null}]}'
                ),
            )
        )
        first = command(kinds=frozenset({MemoryKind.FACT}), key="fact-command-1")
        second = command(
            kinds=frozenset({MemoryKind.FACT}),
            key="fact-command-2",
            event_timestamp=first.event_timestamp + timedelta(minutes=1),
        )
        third = command(
            kinds=frozenset({MemoryKind.FACT}),
            key="fact-command-3",
            event_timestamp=first.event_timestamp + timedelta(minutes=2),
        )
        fourth = command(
            kinds=frozenset({MemoryKind.FACT}),
            key="fact-command-4",
            event_timestamp=first.event_timestamp + timedelta(minutes=3),
        )
        async with engine:
            added = await engine.add(first)
            first_id = added.kind_results[0].mutations[0].memory_id
            updated = await engine.add(second)
            deleted = await engine.add(third)
            noop = await engine.add(fourth)
            page = await store.query(MemoryQuery(scope=first.scope, limit=10))

        assert first_id is not None
        assert added.kind_results[0].mutations[0].event is MemoryEvent.ADD
        assert updated.kind_results[0].mutations[0].event is MemoryEvent.UPDATE
        assert updated.kind_results[0].mutations[0].memory_id == first_id
        assert deleted.kind_results[0].mutations[0].event is MemoryEvent.DELETE
        assert deleted.kind_results[0].mutations[0].memory_id == first_id
        assert noop.kind_results[0].status is KindResultStatus.NO_CHANGE
        assert noop.kind_results[0].mutations[0].event is MemoryEvent.NOOP
        assert page.items == ()

    asyncio.run(scenario())


def test_same_summary_command_replay_does_not_duplicate_the_record() -> None:
    async def scenario() -> None:
        first_summary = '{"schema_version":"summary.v1","summary":"Stable synthetic summary"}'
        changed_output = '{"schema_version":"summary.v1","summary":"Nondeterministic output"}'
        engine, store, model, _, _ = build_engine(
            (response(first_summary), response(changed_output))
        )
        value = command(
            kinds=frozenset({MemoryKind.SUMMARY}),
            key="replayed-summary-command",
        )
        async with engine:
            first = await engine.add(value)
            replay = await engine.add(value)
            page = await store.query(
                MemoryQuery(scope=value.scope, kinds=frozenset({MemoryKind.SUMMARY}))
            )

        assert first.kind_results[0].mutations[0].status is MutationStatus.APPLIED
        assert replay.kind_results[0].mutations[0].status is MutationStatus.REPLAYED
        assert first.kind_results[0].mutations[0].memory_id == (
            replay.kind_results[0].mutations[0].memory_id
        )
        assert len(page.items) == 1
        assert page.items[0].record.content == "Stable synthetic summary"
        assert model.call_count == 2

    asyncio.run(scenario())


def test_reusing_summary_key_with_changed_input_conflicts() -> None:
    async def scenario() -> None:
        engine, store, _, _, _ = build_engine(
            (
                response('{"schema_version":"summary.v1","summary":"First summary"}'),
                response('{"schema_version":"summary.v1","summary":"Second summary"}'),
            )
        )
        first = command(
            kinds=frozenset({MemoryKind.SUMMARY}),
            key="changed-payload-command",
            messages=(ConversationMessage("user", "First synthetic input"),),
        )
        changed = command(
            kinds=frozenset({MemoryKind.SUMMARY}),
            key="changed-payload-command",
            messages=(ConversationMessage("user", "Changed synthetic input"),),
        )
        async with engine:
            await engine.add(first)
            result = await engine.add(changed)
            page = await store.query(
                MemoryQuery(scope=first.scope, kinds=frozenset({MemoryKind.SUMMARY}))
            )

        assert result.kind_results[0].status is KindResultStatus.CONFLICT
        assert result.kind_results[0].issue is not None
        assert result.kind_results[0].issue.code is IssueCode.IDEMPOTENCY_CONFLICT
        assert page.items[0].record.content == "First summary"

    asyncio.run(scenario())


def test_same_fact_add_replays_the_atomic_store_result() -> None:
    async def scenario() -> None:
        extraction = response('{"schema_version":"fact.v1","facts":["Carries a paper map"]}')
        update = response(
            '{"schema_version":"update.v1","operations":['
            '{"event":"add","memory_id":null,"content":"Carries a paper map"}]}'
        )
        engine, store, _, _, _ = build_engine((extraction, update, extraction, update))
        value = command(
            kinds=frozenset({MemoryKind.FACT}),
            key="replayed-fact-command",
        )
        async with engine:
            first = await engine.add(value)
            replay = await engine.add(value)
            page = await store.query(
                MemoryQuery(scope=value.scope, kinds=frozenset({MemoryKind.FACT}))
            )

        assert first.kind_results[0].mutations[0].status is MutationStatus.APPLIED
        assert replay.kind_results[0].mutations[0].status is MutationStatus.REPLAYED
        assert len(page.items) == 1
        assert page.items[0].record.version == 1
        assert first.kind_results[0].mutations[0].memory_id == (
            replay.kind_results[0].mutations[0].memory_id
        )

    asyncio.run(scenario())


def test_store_put_status_remains_provider_independent() -> None:
    assert PutStatus.CREATED.value == "created"
