"""Golden and failure tests for strict untrusted-output parsing."""

from __future__ import annotations

import logging

import pytest

from portable_memory_engine.domain import MemoryEvent, ProviderParseError
from portable_memory_engine.prompts import (
    MAX_RAW_OUTPUT_LENGTH,
    FactOutput,
    OutputSchemaVersion,
    ParseFailureReason,
    ProfileOutput,
    SummaryOutput,
    UpdateOutput,
    parse_prompt_output,
)


@pytest.mark.parametrize(
    ("raw", "version", "expected_type"),
    [
        (
            '{"schema_version":"summary.v1","summary":"Synthetic meeting notes"}',
            OutputSchemaVersion.SUMMARY_V1,
            SummaryOutput,
        ),
        (
            '{"schema_version":"fact.v1","facts":["Uses metric units"]}',
            OutputSchemaVersion.FACT_V1,
            FactOutput,
        ),
        (
            '{"schema_version":"profile.v1","profile":"Prefers concise replies"}',
            OutputSchemaVersion.PROFILE_V1,
            ProfileOutput,
        ),
        (
            """{"schema_version":"update.v1","operations":[
                {"event":"update","memory_id":"memory-7","content":"Prefers tea"},
                {"event":"delete","memory_id":"memory-8","content":null}
            ]}""",
            OutputSchemaVersion.UPDATE_V1,
            UpdateOutput,
        ),
    ],
)
def test_synthetic_golden_outputs_parse_strictly(
    raw: str, version: OutputSchemaVersion, expected_type: type[object]
) -> None:
    result = parse_prompt_output(raw, schema_version=version)

    assert isinstance(result, expected_type)
    if isinstance(result, UpdateOutput):
        assert [operation.event for operation in result.operations] == [
            MemoryEvent.UPDATE,
            MemoryEvent.DELETE,
        ]


@pytest.mark.parametrize("language", ["json", "JSON", ""])
def test_one_complete_markdown_fence_is_accepted(language: str) -> None:
    result = parse_prompt_output(
        f'```{language}\n{{"schema_version":"fact.v1","facts":[]}}\n```',
        schema_version=OutputSchemaVersion.FACT_V1,
    )

    assert result == FactOutput(())


def test_profile_null_represents_insufficient_evidence() -> None:
    result = parse_prompt_output(
        '{"schema_version":"profile.v1","profile":null}',
        schema_version=OutputSchemaVersion.PROFILE_V1,
    )

    assert result == ProfileOutput(None)


def test_backticks_inside_json_text_are_not_treated_as_an_outer_fence() -> None:
    result = parse_prompt_output(
        '{"schema_version":"summary.v1","summary":"Discussed ``` markers"}',
        schema_version=OutputSchemaVersion.SUMMARY_V1,
    )

    assert result == SummaryOutput("Discussed ``` markers")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", ParseFailureReason.EMPTY),
        ("   ", ParseFailureReason.EMPTY),
        ("not json", ParseFailureReason.INVALID_JSON),
        ("{broken", ParseFailureReason.INVALID_JSON),
        (
            'prefix {"schema_version":"fact.v1","facts":[]}',
            ParseFailureReason.EXTRA_TEXT,
        ),
        (
            '{"schema_version":"fact.v1","facts":[]} suffix',
            ParseFailureReason.EXTRA_TEXT,
        ),
        (
            '```json\n{"schema_version":"fact.v1","facts":[]}\n```\nextra',
            ParseFailureReason.EXTRA_TEXT,
        ),
        (
            '```python\n{"schema_version":"fact.v1","facts":[]}\n```',
            ParseFailureReason.EXTRA_TEXT,
        ),
        ("```json\n\n```", ParseFailureReason.EMPTY),
        (
            '{"schema_version":"summary.v1","facts":[]}',
            ParseFailureReason.VERSION_MISMATCH,
        ),
        (
            '{"schema_version":"fact.v1","facts":[],"extra":true}',
            ParseFailureReason.SCHEMA_MISMATCH,
        ),
        (
            '{"schema_version":"fact.v1","facts":["same","same"]}',
            ParseFailureReason.SCHEMA_MISMATCH,
        ),
        (
            '{"schema_version":"fact.v1","facts":[NaN]}',
            ParseFailureReason.INVALID_JSON,
        ),
        (
            '{"schema_version":"fact.v1","facts":[],"facts":[]}',
            ParseFailureReason.INVALID_JSON,
        ),
    ],
)
def test_invalid_output_has_a_bounded_reason(raw: str, reason: ParseFailureReason) -> None:
    with pytest.raises(ProviderParseError) as captured:
        parse_prompt_output(raw, schema_version=OutputSchemaVersion.FACT_V1)

    assert captured.value.schema_version == "fact.v1"
    assert captured.value.reason_code == reason.value
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "operation",
    [
        '{"event":"add","memory_id":"unexpected","content":"fact"}',
        '{"event":"update","memory_id":null,"content":"fact"}',
        '{"event":"delete","memory_id":"memory-1","content":"fact"}',
        '{"event":"noop","memory_id":null,"content":"fact"}',
        '{"event":"rename","memory_id":null,"content":null}',
    ],
)
def test_update_parser_rejects_invalid_event_semantics(operation: str) -> None:
    raw = f'{{"schema_version":"update.v1","operations":[{operation}]}}'

    with pytest.raises(ProviderParseError) as captured:
        parse_prompt_output(raw, schema_version=OutputSchemaVersion.UPDATE_V1)

    assert captured.value.reason_code == ParseFailureReason.SCHEMA_MISMATCH.value


def test_oversized_output_is_rejected_before_json_decode() -> None:
    with pytest.raises(ProviderParseError) as captured:
        parse_prompt_output(
            "{" + "x" * MAX_RAW_OUTPUT_LENGTH,
            schema_version=OutputSchemaVersion.SUMMARY_V1,
        )

    assert captured.value.reason_code == ParseFailureReason.SIZE_LIMIT.value


def test_parse_failure_neither_logs_nor_echoes_raw_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_marker = "synthetic-secret-do-not-echo"
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ProviderParseError) as captured:
        parse_prompt_output(
            f'prefix {{"schema_version":"fact.v1","facts":["{secret_marker}"]}}',
            schema_version=OutputSchemaVersion.FACT_V1,
        )

    assert secret_marker not in str(captured.value)
    assert secret_marker not in repr(captured.value)
    assert secret_marker not in caplog.text
