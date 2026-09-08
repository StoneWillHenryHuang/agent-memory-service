"""Strict parsing of untrusted model output into public prompt values."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Never, cast

from portable_memory_engine.domain import (
    DomainValidationError,
    MemoryEvent,
    ProviderParseError,
)
from portable_memory_engine.prompts.schemas import (
    MAX_OUTPUT_ITEMS,
    FactOutput,
    OutputSchemaVersion,
    ParsedPromptOutput,
    ProfileOutput,
    SummaryOutput,
    UpdateOperation,
    UpdateOutput,
)

MAX_RAW_OUTPUT_LENGTH = 1_000_000
_FENCE = re.compile(r"```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```", re.DOTALL | re.IGNORECASE)


class ParseFailureReason(StrEnum):
    """Bounded, content-free reason codes for prompt parse failures."""

    EMPTY = "empty"
    INVALID_JSON = "invalid_json"
    EXTRA_TEXT = "extra_text"
    SCHEMA_MISMATCH = "schema_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    SIZE_LIMIT = "size_limit"


def _fail(version: OutputSchemaVersion, reason: ParseFailureReason) -> Never:
    raise ProviderParseError(schema_version=version.value, reason_code=reason.value)


def _reject_constant(_value: str) -> Never:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _json_text(raw: str, version: OutputSchemaVersion) -> str:
    if not isinstance(raw, str):
        _fail(version, ParseFailureReason.EMPTY)
    if len(raw) > MAX_RAW_OUTPUT_LENGTH:
        _fail(version, ParseFailureReason.SIZE_LIMIT)
    if not raw.strip():
        _fail(version, ParseFailureReason.EMPTY)
    text = raw.strip()
    if text.startswith("```"):
        match = _FENCE.fullmatch(text)
        if match is None:
            _fail(version, ParseFailureReason.EXTRA_TEXT)
        text = match.group("body").strip()
        if not text:
            _fail(version, ParseFailureReason.EMPTY)
    return text


def _decode(raw: str, version: OutputSchemaVersion) -> object:
    text = _json_text(raw, version)
    if text[0] not in "[{":
        reason = (
            ParseFailureReason.EXTRA_TEXT
            if "{" in text or "[" in text
            else ParseFailureReason.INVALID_JSON
        )
        _fail(version, reason)
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    decode_failed = False
    value: object = None
    end = 0
    try:
        value, end = cast("tuple[object, int]", decoder.raw_decode(text))
    except (json.JSONDecodeError, ValueError):
        decode_failed = True
    if decode_failed:
        _fail(version, ParseFailureReason.INVALID_JSON)
    if text[end:].strip():
        _fail(version, ParseFailureReason.EXTRA_TEXT)
    return value


def _object(value: object, version: OutputSchemaVersion) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _fail(version, ParseFailureReason.SCHEMA_MISMATCH)
    return cast("Mapping[str, object]", value)


def _exact_keys(
    value: Mapping[str, object], expected: set[str], version: OutputSchemaVersion
) -> None:
    if set(value) != expected:
        _fail(version, ParseFailureReason.SCHEMA_MISMATCH)


def _version(value: Mapping[str, object], expected: OutputSchemaVersion) -> None:
    actual = value.get("schema_version")
    if actual != expected.value:
        _fail(expected, ParseFailureReason.VERSION_MISMATCH)


def _construct(
    factory: type[SummaryOutput] | type[FactOutput] | type[ProfileOutput],
    value: object,
    version: OutputSchemaVersion,
) -> SummaryOutput | FactOutput | ProfileOutput:
    try:
        if factory is SummaryOutput and isinstance(value, str):
            return SummaryOutput(value)
        if factory is ProfileOutput and (value is None or isinstance(value, str)):
            return ProfileOutput(value)
        if (
            factory is FactOutput
            and isinstance(value, list)
            and len(value) <= MAX_OUTPUT_ITEMS
            and all(isinstance(item, str) for item in value)
        ):
            return FactOutput(cast("list[str]", value))
    except DomainValidationError:
        pass
    _fail(version, ParseFailureReason.SCHEMA_MISMATCH)


def _parse_simple(
    root: Mapping[str, object], version: OutputSchemaVersion
) -> SummaryOutput | FactOutput | ProfileOutput:
    if version is OutputSchemaVersion.SUMMARY_V1:
        _exact_keys(root, {"schema_version", "summary"}, version)
        return _construct(SummaryOutput, root["summary"], version)
    if version is OutputSchemaVersion.FACT_V1:
        _exact_keys(root, {"schema_version", "facts"}, version)
        return _construct(FactOutput, root["facts"], version)
    _exact_keys(root, {"schema_version", "profile"}, version)
    return _construct(ProfileOutput, root["profile"], version)


def _optional_string(value: object, version: OutputSchemaVersion) -> str | None:
    if value is None or isinstance(value, str):
        return value
    _fail(version, ParseFailureReason.SCHEMA_MISMATCH)


def _parse_update(root: Mapping[str, object]) -> UpdateOutput:
    version = OutputSchemaVersion.UPDATE_V1
    _exact_keys(root, {"schema_version", "operations"}, version)
    values = root["operations"]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_OUTPUT_ITEMS:
        _fail(version, ParseFailureReason.SCHEMA_MISMATCH)
    operations: list[UpdateOperation] = []
    for item in values:
        operation = _object(item, version)
        _exact_keys(operation, {"event", "memory_id", "content"}, version)
        event_value = operation["event"]
        if not isinstance(event_value, str):
            _fail(version, ParseFailureReason.SCHEMA_MISMATCH)
        invalid_operation = False
        try:
            event = MemoryEvent(event_value)
            operations.append(
                UpdateOperation(
                    event=event,
                    memory_id=_optional_string(operation["memory_id"], version),
                    content=_optional_string(operation["content"], version),
                )
            )
        except (DomainValidationError, ValueError):
            invalid_operation = True
        if invalid_operation:
            _fail(version, ParseFailureReason.SCHEMA_MISMATCH)
    try:
        return UpdateOutput(operations)
    except DomainValidationError:
        _fail(version, ParseFailureReason.SCHEMA_MISMATCH)


def parse_prompt_output(raw: str, *, schema_version: OutputSchemaVersion) -> ParsedPromptOutput:
    """Parse one complete JSON object without retaining or logging raw content.

    A plain JSON object or one complete ``json`` Markdown fence is accepted.
    Preambles, trailing prose, multiple fences, unknown fields, and invalid
    version/shape combinations are rejected with content-free reason codes.
    """

    if not isinstance(schema_version, OutputSchemaVersion):
        raise DomainValidationError("schema_version must be OutputSchemaVersion")
    root = _object(_decode(raw, schema_version), schema_version)
    _version(root, schema_version)
    if schema_version is OutputSchemaVersion.UPDATE_V1:
        return _parse_update(root)
    return _parse_simple(root, schema_version)
