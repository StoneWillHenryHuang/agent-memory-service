"""Tests for public application commands, results, and deterministic identity."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast

import pytest

from portable_memory_engine.application import (
    AddMemoryCommand,
    AddMemoryResult,
    AddResultStatus,
    DefaultIdentityStrategy,
    ExtractionIssue,
    ExtractionLimits,
    IssueCode,
    KindAddResult,
    KindResultStatus,
    MemoryMutation,
    MutationStatus,
    PromptOverrides,
    RoleBasedUserMessageFilter,
)
from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    MemoryEvent,
    MemoryKind,
    MemoryScope,
)
from portable_memory_engine.ports import PromptTemplate
from tests.unit.application._helpers import EVENT_TIME, SCOPE, command


def test_default_identity_has_stable_public_v1_goldens() -> None:
    strategy = DefaultIdentityStrategy()

    assert strategy.summary_id(scope=SCOPE, session_id="synthetic-session-1") == (
        "summary-v1-a68eafaadf3bef284307f3856d4ab94d6929b805a26ed1e1ac3d8bfa98c31031"
    )
    assert strategy.fact_id(scope=SCOPE, content="  Morning   Trains ") == (
        "fact-v1-bdb707e4fa02f9b87aebdc071099ffde342b3aa293507c900721eaedd07be0a8"
    )
    assert strategy.profile_id(scope=SCOPE, profile_name="default") == (
        "profile-v1-f054ff0c4aa67a23c82b848aebd66cefc8f23553c32d7f9732c39b4b5a4138b1"
    )
    assert strategy.fact_canonical("  MORNING\ttrains ") == "morning trains"
    assert strategy.fact_id(scope=SCOPE, content="Morning trains") == strategy.fact_id(
        scope=SCOPE,
        content=" morning  TRAINS ",
    )
    assert strategy.summary_id(
        scope=MemoryScope(subject_id="another", namespace="synthetic-app"),
        session_id="synthetic-session-1",
    ) != strategy.summary_id(scope=SCOPE, session_id="synthetic-session-1")


def test_add_command_copies_inputs_normalizes_time_and_hides_content() -> None:
    messages = [ConversationMessage(role="user", content="Synthetic private message")]
    value = AddMemoryCommand(
        scope=SCOPE,
        messages=messages,
        idempotency_key="command-1",
        event_timestamp=EVENT_TIME,
        kinds=frozenset({MemoryKind.FACT}),
        locale="EN-gb",
        metadata={"synthetic": (1, 2)},
    )
    messages.clear()

    assert len(value.messages) == 1
    assert value.locale == "en-gb"
    assert value.event_timestamp == EVENT_TIME
    assert value.metadata["synthetic"] == (1, 2)
    assert "Synthetic private message" not in repr(value)
    assert "command-1" not in repr(value)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AddMemoryCommand(SCOPE, (), "key", EVENT_TIME, kinds=frozenset({MemoryKind.FACT})),
        lambda: AddMemoryCommand(
            SCOPE,
            (ConversationMessage("user", "message"),),
            "key",
            EVENT_TIME,
            kinds=frozenset({MemoryKind.SUMMARY}),
        ),
        lambda: AddMemoryCommand(
            SCOPE,
            (ConversationMessage("user", "message"),),
            "key",
            datetime(2026, 7, 31),
            kinds=frozenset({MemoryKind.FACT}),
        ),
        lambda: AddMemoryCommand(
            SCOPE,
            (ConversationMessage("user", "message"),),
            "key",
            EVENT_TIME,
            kinds=frozenset({MemoryKind.FACT}),
            locale="bad_locale",
        ),
        lambda: ExtractionLimits(max_messages=0),
        lambda: ExtractionLimits(max_existing_memories=1001),
        lambda: ExtractionLimits(max_conflict_retries=11),
        lambda: RoleBasedUserMessageFilter(frozenset()),
    ],
)
def test_application_values_reject_invalid_configuration(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(DomainValidationError):
        factory()


def test_prompt_overrides_reject_non_templates() -> None:
    with pytest.raises(DomainValidationError, match="summary"):
        PromptOverrides(summary=cast("PromptTemplate", object()))


def test_structured_results_derive_success_partial_and_failure() -> None:
    success = KindAddResult(
        MemoryKind.SUMMARY,
        KindResultStatus.SUCCEEDED,
        (
            MemoryMutation(
                MemoryKind.SUMMARY,
                MemoryEvent.ADD,
                MutationStatus.APPLIED,
                "summary-1",
            ),
        ),
    )
    partial = KindAddResult(
        MemoryKind.FACT,
        KindResultStatus.PARTIAL,
        (
            MemoryMutation(
                MemoryKind.FACT,
                MemoryEvent.ADD,
                MutationStatus.APPLIED,
                "fact-1",
            ),
        ),
        ExtractionIssue(IssueCode.CONFLICT, retryable=True),
    )
    failed = KindAddResult(
        MemoryKind.PROFILE,
        KindResultStatus.FAILED,
        issue=ExtractionIssue(IssueCode.PROVIDER_PARSE),
    )

    assert (
        AddMemoryResult.from_kind_results(
            scope=SCOPE,
            idempotency_key="key-1",
            kind_results=(success,),
        ).status
        is AddResultStatus.SUCCEEDED
    )
    assert (
        AddMemoryResult.from_kind_results(
            scope=SCOPE,
            idempotency_key="key-2",
            kind_results=(partial,),
        ).status
        is AddResultStatus.PARTIAL
    )
    assert (
        AddMemoryResult.from_kind_results(
            scope=SCOPE,
            idempotency_key="key-3",
            kind_results=(failed,),
        ).status
        is AddResultStatus.FAILED
    )
    assert (
        AddMemoryResult.from_kind_results(
            scope=SCOPE,
            idempotency_key="key-4",
            kind_results=(success, failed),
        ).status
        is AddResultStatus.PARTIAL
    )
    assert "summary-1" not in repr(success)


def test_result_values_reject_inconsistent_shapes() -> None:
    issue = ExtractionIssue(IssueCode.CONFLICT)
    with pytest.raises(DomainValidationError, match="require memory_id"):
        MemoryMutation(MemoryKind.FACT, MemoryEvent.ADD, MutationStatus.APPLIED)
    with pytest.raises(DomainValidationError, match="require an issue"):
        MemoryMutation(
            MemoryKind.FACT,
            MemoryEvent.DELETE,
            MutationStatus.FAILED,
            "fact-1",
        )
    with pytest.raises(DomainValidationError, match="must agree"):
        KindAddResult(MemoryKind.FACT, KindResultStatus.FAILED)
    with pytest.raises(DomainValidationError, match="does not match"):
        AddMemoryResult(
            SCOPE,
            AddResultStatus.SUCCEEDED,
            (KindAddResult(MemoryKind.FACT, KindResultStatus.FAILED, issue=issue),),
            "key",
        )


def test_default_command_requests_all_three_kinds() -> None:
    value = command()
    assert value.kinds == frozenset((MemoryKind.SUMMARY, MemoryKind.FACT, MemoryKind.PROFILE))
