"""Tests for versioned prompt schemas and parsed output values."""

from __future__ import annotations

from typing import cast

import pytest

from portable_memory_engine.domain import DomainValidationError, MemoryEvent
from portable_memory_engine.prompts import (
    FACT_OUTPUT_SCHEMA,
    PROFILE_OUTPUT_SCHEMA,
    SUMMARY_OUTPUT_SCHEMA,
    UPDATE_OUTPUT_SCHEMA,
    FactOutput,
    OutputSchemaVersion,
    ProfileOutput,
    SummaryOutput,
    UpdateOperation,
    UpdateOutput,
    output_schema,
)


def test_every_builtin_output_schema_has_a_stable_version() -> None:
    assert [version.value for version in OutputSchemaVersion] == [
        "summary.v1",
        "fact.v1",
        "profile.v1",
        "update.v1",
    ]
    assert output_schema(OutputSchemaVersion.SUMMARY_V1) is SUMMARY_OUTPUT_SCHEMA
    assert output_schema(OutputSchemaVersion.FACT_V1) is FACT_OUTPUT_SCHEMA
    assert output_schema(OutputSchemaVersion.PROFILE_V1) is PROFILE_OUTPUT_SCHEMA
    assert output_schema(OutputSchemaVersion.UPDATE_V1) is UPDATE_OUTPUT_SCHEMA
    assert SUMMARY_OUTPUT_SCHEMA["additionalProperties"] is False
    with pytest.raises(DomainValidationError, match="OutputSchemaVersion"):
        output_schema(cast("OutputSchemaVersion", "summary.v1"))


def test_parsed_content_is_immutable_normalized_and_hidden() -> None:
    summary = SummaryOutput("Cafe\N{COMBINING ACUTE ACCENT} notes")
    facts = FactOutput(["Uses the train", "Prefers morning meetings"])
    profile = ProfileOutput("Enjoys quiet workspaces")

    assert summary.summary == "Caf\N{LATIN SMALL LETTER E WITH ACUTE} notes"
    assert facts.facts == ("Uses the train", "Prefers morning meetings")
    assert "train" not in repr(facts)
    assert "workspaces" not in repr(profile)
    assert ProfileOutput(None).profile is None


@pytest.mark.parametrize(
    ("event", "memory_id", "content"),
    [
        (MemoryEvent.ADD, None, "Uses metric units"),
        (MemoryEvent.UPDATE, "memory-1", "Now uses imperial units"),
        (MemoryEvent.DELETE, "memory-1", None),
        (MemoryEvent.NOOP, None, None),
    ],
)
def test_update_operations_accept_only_event_specific_shapes(
    event: MemoryEvent, memory_id: str | None, content: str | None
) -> None:
    operation = UpdateOperation(event, memory_id, content)

    assert operation.event is event
    assert "metric" not in repr(operation)
    assert "memory-1" not in repr(operation)


@pytest.mark.parametrize(
    ("event", "memory_id", "content"),
    [
        (MemoryEvent.ADD, "memory-1", "content"),
        (MemoryEvent.ADD, None, None),
        (MemoryEvent.UPDATE, None, "content"),
        (MemoryEvent.DELETE, "memory-1", "content"),
        (MemoryEvent.NOOP, None, "content"),
    ],
)
def test_update_operations_reject_ambiguous_shapes(
    event: MemoryEvent, memory_id: str | None, content: str | None
) -> None:
    with pytest.raises(DomainValidationError):
        UpdateOperation(event, memory_id, content)


def test_outputs_reject_duplicates_invalid_sizes_and_mixed_noop() -> None:
    with pytest.raises(DomainValidationError, match="sequence"):
        FactOutput("not-a-sequence")
    with pytest.raises(DomainValidationError, match="duplicate"):
        FactOutput(("same", "same"))
    with pytest.raises(DomainValidationError, match="non-empty"):
        SummaryOutput(" ")
    with pytest.raises(DomainValidationError, match="1 to 100"):
        UpdateOutput(())
    with pytest.raises(DomainValidationError, match="only"):
        UpdateOutput(
            (
                UpdateOperation(MemoryEvent.NOOP),
                UpdateOperation(MemoryEvent.ADD, content="new fact"),
            )
        )
