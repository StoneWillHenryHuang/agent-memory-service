"""Concurrency and transaction tests for the in-memory adapter."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from portable_memory_engine.adapters import InMemoryMemoryStore
from portable_memory_engine.domain import (
    CapabilityError,
    ConflictError,
    DeleteMemoryCommand,
    DeleteScopeCommand,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    IdempotencyConflictError,
    MemoryKind,
    MemoryQuery,
    PutResult,
    SemanticQuery,
    StaleEventError,
)
from tests.unit.adapters._factories import NOW, make_record, make_scope, make_write


def test_capabilities_truthfully_describe_the_implementation() -> None:
    capabilities = InMemoryMemoryStore().capabilities

    assert capabilities.vector_search is True
    assert capabilities.atomic_compare_and_swap is True
    assert capabilities.atomic_idempotency is True
    assert capabilities.freshness_watermarks is True
    assert capabilities.deletion_barriers is True
    assert capabilities.transactions is True
    assert capabilities.exact_total_count is True
    assert capabilities.metadata_filtering is False
    assert capabilities.subject_deletion is True
    assert capabilities.session_contributions is True


def test_concurrent_writers_have_one_winner_without_lost_update() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            for index in range(25):
                original = make_record(f"concurrent-{index}")
                created = await store.put(make_write(original, key=f"create-{index}"))
                version = created.outcomes[0].version
                later = NOW + timedelta(seconds=1)
                first = make_write(
                    replace(original, content="first", updated_at=later),
                    key=f"writer-first-{index}",
                    timestamp=later,
                    expected_version=version,
                )
                second = make_write(
                    replace(original, content="second", updated_at=later),
                    key=f"writer-second-{index}",
                    timestamp=later,
                    expected_version=version,
                )

                results = await asyncio.gather(
                    store.put(first),
                    store.put(second),
                    return_exceptions=True,
                )
                successes = [result for result in results if isinstance(result, PutResult)]
                conflicts = [result for result in results if isinstance(result, ConflictError)]
                assert len(successes) == 1
                assert len(conflicts) == 1
                stored = await store.get(scope=original.scope, memory_id=original.id)
                assert stored is not None
                assert stored.content in {"first", "second"}
                assert stored.version == successes[0].outcomes[0].version
        finally:
            await store.close()

    asyncio.run(scenario())


def test_concurrent_idempotent_replay_commits_once() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            command = make_write(make_record("replay"), key="same-command")
            results = await asyncio.gather(*(store.put(command) for _ in range(20)))
            outcomes = [result.outcomes[0] for result in results]
            assert sum(not outcome.replayed for outcome in outcomes) == 1
            assert sum(outcome.replayed for outcome in outcomes) == 19
            assert len({outcome.version for outcome in outcomes}) == 1
            page = await store.query(MemoryQuery(scope=command.record.scope))
            assert [match.record.id for match in page.items] == ["replay"]
        finally:
            await store.close()

    asyncio.run(scenario())


def test_atomic_write_group_rolls_back_control_and_content_state() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            existing = make_record("existing")
            await store.put(make_write(existing, key="existing-create"))
            later = NOW + timedelta(seconds=1)
            commands = (
                make_write(make_record("new", timestamp=later), key="atomic-new"),
                make_write(
                    replace(existing, updated_at=later),
                    key="atomic-conflict",
                    timestamp=later,
                ),
            )
            with pytest.raises(ConflictError):
                await store.put_many(commands, atomic=True)
            assert await store.get(scope=existing.scope, memory_id="new") is None
            replayed = await store.put(commands[0])
            assert replayed.outcomes[0].replayed is False
        finally:
            await store.close()

    asyncio.run(scenario())


def test_non_atomic_group_keeps_committed_prefix_and_keys_cannot_cross_operations() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            existing = make_record("existing-non-atomic")
            await store.put(make_write(existing, key="existing-non-atomic-create"))
            later = NOW + timedelta(seconds=1)
            new_command = make_write(
                make_record("prefix-commit", timestamp=later),
                key="shared-operation-key",
            )
            commands = (
                new_command,
                make_write(
                    replace(existing, updated_at=later),
                    key="non-atomic-conflict",
                    timestamp=later,
                ),
            )
            with pytest.raises(ConflictError):
                await store.put_many(commands, atomic=False)
            assert await store.get(scope=existing.scope, memory_id="prefix-commit") is not None
            with pytest.raises(IdempotencyConflictError):
                await store.delete(
                    DeleteMemoryCommand(
                        scope=existing.scope,
                        memory_id="prefix-commit",
                        idempotency_key="shared-operation-key",
                        payload_digest=new_command.payload_digest,
                        event_timestamp=later + timedelta(seconds=1),
                    )
                )
        finally:
            await store.close()

    asyncio.run(scenario())


def test_scope_isolation_survives_concurrent_creation() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            scopes = tuple(make_scope(f"subject-{index}") for index in range(30))
            await asyncio.gather(
                *(
                    store.put(
                        make_write(
                            make_record("shared", scope=scope),
                            key=f"create-{index}",
                        )
                    )
                    for index, scope in enumerate(scopes)
                )
            )
            pages = await asyncio.gather(
                *(store.query(MemoryQuery(scope=scope)) for scope in scopes)
            )
            assert all(len(page.items) == 1 for page in pages)
            assert {page.items[0].record.scope for page in pages} == set(scopes)
        finally:
            await store.close()

    asyncio.run(scenario())


def test_semantic_search_rejects_incompatible_dimensions() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            document = replace(
                make_record("embedded"),
                embedding=Embedding(
                    values=(1.0, 0.0),
                    model_id="model-v1",
                    task=EmbeddingTask.DOCUMENT,
                ),
            )
            await store.put(make_write(document, key="embedded-create"))
            with pytest.raises(CapabilityError, match="compatible_embedding_dimension"):
                await store.semantic_search(
                    SemanticQuery(scope=document.scope, text="query"),
                    query_embedding=Embedding(
                        values=(1.0, 0.0, 0.0),
                        model_id="model-v1",
                        task=EmbeddingTask.QUERY,
                    ),
                )
        finally:
            await store.close()

    asyncio.run(scenario())


def test_semantic_pagination_threshold_and_query_task_are_enforced() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            for memory_id, values in (("zero", (0.0, 0.0)), ("unit", (1.0, 0.0))):
                record = replace(
                    make_record(memory_id),
                    embedding=Embedding(
                        values=values,
                        model_id="model-v1",
                        task=EmbeddingTask.DOCUMENT,
                    ),
                )
                await store.put(make_write(record, key=f"{memory_id}-create"))
            with pytest.raises(DomainValidationError, match="query embedding"):
                await store.semantic_search(
                    SemanticQuery(scope=make_scope(), text="query"),
                    query_embedding=Embedding(
                        values=(0.0, 0.0),
                        model_id="model-v1",
                        task=EmbeddingTask.DOCUMENT,
                    ),
                )

            query_embedding = Embedding(
                values=(0.0, 0.0),
                model_id="model-v1",
                task=EmbeddingTask.QUERY,
            )
            page = await store.semantic_search(
                SemanticQuery(scope=make_scope(), text="query", limit=1),
                query_embedding=query_embedding,
            )
            assert len(page.items) == 1
            assert page.next_offset == 1
            filtered = await store.semantic_search(
                SemanticQuery(scope=make_scope(), text="query", min_score=0.1),
                query_embedding=query_embedding,
            )
            assert filtered.items == ()
        finally:
            await store.close()

    asyncio.run(scenario())


def test_id_and_scope_barriers_reject_competing_deletions_and_recreation() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            record = make_record("barrier")
            await store.put(make_write(record, key="barrier-create"))
            deletion_time = NOW + timedelta(seconds=1)
            await store.delete(
                DeleteMemoryCommand(
                    scope=record.scope,
                    memory_id=record.id,
                    idempotency_key="barrier-delete-id",
                    payload_digest="digest:barrier-delete-id",
                    event_timestamp=deletion_time,
                )
            )
            repeated_id = await store.delete(
                DeleteMemoryCommand(
                    scope=record.scope,
                    memory_id=record.id,
                    idempotency_key="barrier-delete-id-competitor",
                    payload_digest="digest:barrier-delete-id-competitor",
                    event_timestamp=deletion_time,
                )
            )
            assert repeated_id.deleted_count == 0
            assert repeated_id.already_absent_count == 1

            scope_time = deletion_time + timedelta(seconds=1)
            await store.delete(
                DeleteScopeCommand(
                    scope=record.scope,
                    idempotency_key="barrier-delete-scope",
                    payload_digest="digest:barrier-delete-scope",
                    event_timestamp=scope_time,
                )
            )
            repeated_scope = await store.delete(
                DeleteScopeCommand(
                    scope=record.scope,
                    idempotency_key="barrier-delete-scope-competitor",
                    payload_digest="digest:barrier-delete-scope-competitor",
                    event_timestamp=scope_time,
                )
            )
            assert repeated_scope.deleted_count == 0
            with pytest.raises(StaleEventError):
                await store.put(
                    make_write(
                        make_record("after-scope", timestamp=scope_time),
                        key="after-scope-stale",
                    )
                )
        finally:
            await store.close()

    asyncio.run(scenario())


def test_memory_kind_cannot_change_during_an_update() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.open()
        try:
            record = make_record("kind")
            result = await store.put(make_write(record, key="kind-create"))
            with pytest.raises(DomainValidationError, match="cannot change memory kind"):
                await store.put(
                    make_write(
                        replace(
                            record,
                            kind=MemoryKind.PROFILE,
                            updated_at=NOW + timedelta(seconds=1),
                        ),
                        key="kind-update",
                        timestamp=NOW + timedelta(seconds=1),
                        expected_version=result.outcomes[0].version,
                    )
                )
        finally:
            await store.close()

    asyncio.run(scenario())
