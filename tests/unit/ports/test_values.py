"""Tests for provider, policy, and observation boundary values."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    EmbeddingTask,
    FrozenJsonObject,
    MemoryKind,
    MemoryScope,
)
from portable_memory_engine.ports import (
    AccessDecision,
    AccessOperation,
    AccessRequest,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    ObservationEvent,
    ObservationOutcome,
    PromptPurpose,
    PromptRequest,
    PromptTemplate,
    TokenUsage,
)


def test_chat_values_are_immutable_and_content_safe() -> None:
    messages = [ConversationMessage(role="user", content="private input")]
    request = ChatRequest(
        messages,
        response_schema=FrozenJsonObject({"type": "object"}),
    )
    response = ChatResponse(
        content="private output",
        model_id="model-v1",
        usage=TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
    )
    messages.clear()

    assert len(request.messages) == 1
    assert response.prompt_tokens + response.completion_tokens == 5
    assert ChatResponse(content="", model_id="model-v1").content == ""
    assert "private input" not in repr(request)
    assert "private output" not in repr(response)


def test_chat_values_reject_invalid_shapes() -> None:
    with pytest.raises(DomainValidationError, match="messages"):
        ChatRequest(())
    with pytest.raises(DomainValidationError, match="response_schema"):
        ChatRequest(
            (ConversationMessage(role="user", content="hello"),),
            response_schema=cast("FrozenJsonObject", object()),
        )
    with pytest.raises(DomainValidationError, match="input_tokens"):
        TokenUsage(input_tokens=-1)
    with pytest.raises(DomainValidationError, match="usage"):
        ChatResponse("output", "model-v1", usage=cast("TokenUsage", object()))
    with pytest.raises(DomainValidationError, match="model_id"):
        ChatResponse("output", "bad\x00model")
    with pytest.raises(DomainValidationError, match="content"):
        ChatResponse("bad\x00output", "model-v1")


def test_embedding_request_copies_text_and_requires_a_task() -> None:
    texts = ["first", "second"]
    request = EmbeddingRequest(texts=texts, task=EmbeddingTask.DOCUMENT)
    texts.clear()

    assert request.texts == ("first", "second")
    assert "first" not in repr(request)
    with pytest.raises(DomainValidationError, match="must not be empty"):
        EmbeddingRequest(texts=(), task=EmbeddingTask.QUERY)
    with pytest.raises(DomainValidationError, match="task"):
        EmbeddingRequest(texts=("query",), task=cast("EmbeddingTask", "query"))


def test_prompt_values_keep_source_outside_scope_and_hide_text() -> None:
    request = PromptRequest(
        purpose=PromptPurpose.EXTRACTION,
        kind=MemoryKind.FACT,
        source="api",
    )
    prompt = PromptTemplate(
        identifier="fact-extraction",
        version="v1",
        system_text="private system text",
        user_text="private user template",
        response_schema_version="v1",
        response_schema=FrozenJsonObject({"type": "object"}),
    )

    assert request.source == "api"
    assert request.locale == "en"
    assert not hasattr(request, "scope")
    assert "private system text" not in repr(prompt)
    assert "private user template" not in repr(prompt)


def test_policy_values_reject_ambiguous_decisions() -> None:
    with pytest.raises(DomainValidationError, match="scope"):
        AccessRequest(cast("MemoryScope", object()), AccessOperation.READ)
    with pytest.raises(DomainValidationError, match="operation"):
        AccessRequest(MemoryScope(subject_id="subject-1"), cast("AccessOperation", "read"))
    with pytest.raises(DomainValidationError, match="must not carry"):
        AccessDecision(True, "unexpected")
    with pytest.raises(DomainValidationError, match="require"):
        AccessDecision(False)
    with pytest.raises(DomainValidationError, match="identifier"):
        AccessDecision(False, "private reason")


def test_observation_event_is_bounded_and_normalizes_utc() -> None:
    event = ObservationEvent(
        operation="memory.put",
        outcome=ObservationOutcome.SUCCEEDED,
        occurred_at=datetime(2026, 7, 30, 16, tzinfo=timezone(timedelta(hours=8))),
        duration_seconds=0.25,
        kind=MemoryKind.FACT,
        item_count=2,
    )

    assert event.occurred_at == datetime(2026, 7, 30, 8, tzinfo=UTC)
    assert not hasattr(event, "content")
    assert not hasattr(event, "scope")


@pytest.mark.parametrize(
    "event",
    [
        lambda: ObservationEvent("bad operation", ObservationOutcome.FAILED, datetime.now(UTC)),
        lambda: ObservationEvent("operation", ObservationOutcome.FAILED, datetime(2026, 7, 30)),
        lambda: ObservationEvent(
            "operation", ObservationOutcome.FAILED, datetime.now(UTC), duration_seconds=-1
        ),
        lambda: ObservationEvent(
            "operation", ObservationOutcome.FAILED, datetime.now(UTC), item_count=-1
        ),
    ],
)
def test_observation_event_rejects_unbounded_values(event: object) -> None:
    factory = cast("Callable[[], ObservationEvent]", event)
    with pytest.raises(DomainValidationError):
        factory()
