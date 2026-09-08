"""Tests for conditional write and deletion command values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from typing import cast

import pytest

from portable_memory_engine.domain import (
    ConditionalWrite,
    DeleteMemoryCommand,
    DeleteResult,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    DeleteTarget,
    DomainValidationError,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemorySubject,
    PutOutcome,
    PutResult,
    PutStatus,
)
from tests.unit.domain._factories import NOW, make_record, make_scope


def test_conditional_write_normalizes_guards_and_hides_command_material() -> None:
    command = ConditionalWrite(
        record=make_record(),
        idempotency_key="request-1",
        payload_digest="sha256:abc",
        event_timestamp=NOW,
        expected_version=2,
    )

    assert command.expected_version == 2
    assert "request-1" not in repr(command)
    assert "sha256:abc" not in repr(command)
    with pytest.raises(FrozenInstanceError):
        command.expected_version = 3  # type: ignore[misc]


def test_conditional_write_rejects_invalid_record_and_versions() -> None:
    with pytest.raises(DomainValidationError, match="record"):
        ConditionalWrite(
            record=cast("MemoryRecord", object()),
            idempotency_key="request-1",
            payload_digest="sha256:abc",
            event_timestamp=NOW,
        )
    with pytest.raises(DomainValidationError, match="negative"):
        ConditionalWrite(
            record=make_record(),
            idempotency_key="request-1",
            payload_digest="sha256:abc",
            event_timestamp=NOW,
            expected_version=-1,
        )
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        ConditionalWrite(
            record=make_record(),
            idempotency_key="request-1",
            payload_digest="sha256:abc",
            event_timestamp=datetime(2026, 7, 30),
        )


def test_put_result_counts_statuses_and_replays() -> None:
    scope = make_scope()
    outcomes = [
        PutOutcome("memory-1", scope, PutStatus.CREATED, 1),
        PutOutcome("memory-2", scope, PutStatus.UPDATED, "etag-2"),
        PutOutcome("memory-3", scope, PutStatus.UNCHANGED, 3, replayed=True),
    ]
    result = PutResult(outcomes)
    outcomes.clear()

    assert isinstance(result.outcomes, tuple)
    assert result.created_count == 1
    assert result.updated_count == 1
    assert result.unchanged_count == 1
    assert result.outcomes[-1].replayed is True


def test_put_outcome_rejects_invalid_values() -> None:
    with pytest.raises(DomainValidationError, match="scope"):
        PutOutcome("memory-1", cast("MemoryScope", object()), PutStatus.CREATED, 1)
    with pytest.raises(DomainValidationError, match="PutStatus"):
        PutOutcome("memory-1", make_scope(), cast("PutStatus", "created"), 1)
    with pytest.raises(DomainValidationError, match="boolean"):
        PutOutcome("memory-1", make_scope(), PutStatus.CREATED, 1, replayed=cast("bool", 1))
    with pytest.raises(DomainValidationError, match="must not be None"):
        PutOutcome("memory-1", make_scope(), PutStatus.CREATED, cast("int", None))


def test_put_result_rejects_invalid_or_duplicate_outcomes() -> None:
    outcome = PutOutcome("memory-1", make_scope(), PutStatus.CREATED, 1)
    with pytest.raises(DomainValidationError, match="PutOutcome"):
        PutResult(cast("tuple[PutOutcome, ...]", (object(),)))
    with pytest.raises(DomainValidationError, match="repeat"):
        PutResult((outcome, outcome))


def test_delete_commands_expose_four_explicit_targets() -> None:
    by_id = DeleteMemoryCommand(
        scope=make_scope(),
        memory_id="memory-1",
        idempotency_key="request-1",
        payload_digest="sha256:abc",
        event_timestamp=NOW,
    )
    by_scope = DeleteScopeCommand(
        scope=make_scope(),
        kinds=frozenset({MemoryKind.FACT}),
        idempotency_key="request-2",
        payload_digest="sha256:def",
        event_timestamp=NOW,
    )
    by_subject = DeleteSubjectCommand(
        subject=MemorySubject(subject_id="subject-1", tenant_id="tenant-1"),
        idempotency_key="request-3",
        payload_digest="sha256:ghi",
        event_timestamp=NOW,
    )
    by_session = DeleteSessionCommand(
        scope=make_scope(),
        session_id="session-1",
        idempotency_key="request-4",
        payload_digest="sha256:jkl",
        event_timestamp=NOW,
    )

    assert by_id.target is DeleteTarget.MEMORY_ID
    assert by_scope.target is DeleteTarget.SCOPE
    assert by_subject.target is DeleteTarget.SUBJECT
    assert by_session.target is DeleteTarget.SESSION
    assert by_scope.kinds == frozenset({MemoryKind.FACT})
    assert "memory-1" not in repr(by_id)
    assert "session-1" not in repr(by_session)
    assert "subject-1" not in repr(by_subject)


def test_delete_commands_reject_invalid_boundaries_and_naive_time() -> None:
    with pytest.raises(DomainValidationError, match="MemorySubject"):
        DeleteSubjectCommand(
            subject=cast("MemorySubject", object()),
            idempotency_key="request-1",
            payload_digest="sha256:abc",
            event_timestamp=NOW,
        )
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        DeleteScopeCommand(
            scope=make_scope(),
            idempotency_key="request-1",
            payload_digest="sha256:abc",
            event_timestamp=datetime(2026, 7, 30),
        )


def test_delete_result_exposes_counts_and_barrier() -> None:
    result = DeleteResult(
        matched_count=2,
        hard_deleted_count=1,
        contribution_deleted_count=1,
        tombstoned_count=1,
        already_absent_count=1,
        barrier_timestamp=NOW,
        replayed=True,
    )

    assert result.deleted_count == 2
    assert result.hard_deleted_count == 1
    assert result.contribution_deleted_count == 1
    assert result.barrier_timestamp == NOW
    assert result.replayed is True


def test_delete_result_rejects_invalid_counts_and_replay_flag() -> None:
    with pytest.raises(DomainValidationError, match="matched_count"):
        DeleteResult(-1, 0, 0, 0, 0, NOW)
    with pytest.raises(DomainValidationError, match="hard_deleted_count"):
        DeleteResult(0, cast("int", True), 0, 0, 0, NOW)
    with pytest.raises(DomainValidationError, match="contribution_deleted_count"):
        DeleteResult(0, 0, -1, 0, 0, NOW)
    with pytest.raises(DomainValidationError, match="tombstoned_count"):
        DeleteResult(0, 0, 0, -1, 0, NOW)
    with pytest.raises(DomainValidationError, match="already_absent_count"):
        DeleteResult(0, 0, 0, 0, -1, NOW)
    with pytest.raises(DomainValidationError, match="deleted counts"):
        DeleteResult(1, 1, 1, 1, 0, NOW)
    with pytest.raises(DomainValidationError, match="tombstoned_count"):
        DeleteResult(1, 0, 1, 2, 0, NOW)
    with pytest.raises(DomainValidationError, match="replayed"):
        DeleteResult(0, 0, 0, 0, 0, NOW, replayed=cast("bool", 1))
