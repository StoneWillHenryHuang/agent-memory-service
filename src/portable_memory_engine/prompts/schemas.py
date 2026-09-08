"""Versioned public output schemas and immutable parsed prompt values."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from portable_memory_engine.domain import (
    DomainValidationError,
    FrozenJsonObject,
    MemoryEvent,
)

MAX_OUTPUT_ITEMS = 100
MAX_OUTPUT_TEXT_LENGTH = 20_000


class OutputSchemaVersion(StrEnum):
    """Stable version identifiers for built-in prompt outputs."""

    SUMMARY_V1 = "summary.v1"
    FACT_V1 = "fact.v1"
    PROFILE_V1 = "profile.v1"
    UPDATE_V1 = "update.v1"


def _text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be text")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized.strip() or len(normalized) > MAX_OUTPUT_TEXT_LENGTH or "\x00" in normalized:
        raise DomainValidationError(
            f"{field_name} must be non-empty text within the public size limit"
        )
    return normalized


def _memory_id(value: str, *, field_name: str) -> str:
    normalized = _text(value, field_name=field_name)
    if (
        len(normalized) > 255
        or normalized == "*"
        or normalized != normalized.strip()
        or any(character.isspace() for character in normalized)
        or any(unicodedata.category(character).startswith("C") for character in normalized)
    ):
        raise DomainValidationError(f"{field_name} must be an identifier")
    return normalized


@dataclass(frozen=True, slots=True)
class SummaryOutput:
    """Parsed v1 summary output with content hidden from representations."""

    summary: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _text(self.summary, field_name="summary"))


@dataclass(frozen=True, slots=True)
class FactOutput:
    """Parsed v1 collection of zero or more durable fact candidates."""

    facts: Sequence[str] = field(repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.facts, str):
            raise DomainValidationError("facts must be a sequence of text values")
        facts = tuple(_text(value, field_name="fact") for value in self.facts)
        if len(facts) > MAX_OUTPUT_ITEMS:
            raise DomainValidationError("facts exceed the public item limit")
        if len(set(facts)) != len(facts):
            raise DomainValidationError("facts must not contain duplicate values")
        object.__setattr__(self, "facts", facts)


@dataclass(frozen=True, slots=True)
class ProfileOutput:
    """Parsed v1 profile text, or ``None`` when evidence is insufficient."""

    profile: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.profile is not None:
            object.__setattr__(self, "profile", _text(self.profile, field_name="profile"))


@dataclass(frozen=True, slots=True)
class UpdateOperation:
    """One strictly validated add, update, delete, or no-op proposal."""

    event: MemoryEvent
    memory_id: str | None = field(default=None, repr=False)
    content: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event, MemoryEvent):
            raise DomainValidationError("update event must be MemoryEvent")
        memory_id = (
            None if self.memory_id is None else _memory_id(self.memory_id, field_name="memory_id")
        )
        content = None if self.content is None else _text(self.content, field_name="update content")
        if self.event is MemoryEvent.ADD and (memory_id is not None or content is None):
            raise DomainValidationError("add requires content and no memory_id")
        if self.event is MemoryEvent.UPDATE and (memory_id is None or content is None):
            raise DomainValidationError("update requires memory_id and content")
        if self.event is MemoryEvent.DELETE and (memory_id is None or content is not None):
            raise DomainValidationError("delete requires memory_id and no content")
        if self.event is MemoryEvent.NOOP and (memory_id is not None or content is not None):
            raise DomainValidationError("noop must not carry memory_id or content")
        object.__setattr__(self, "memory_id", memory_id)
        object.__setattr__(self, "content", content)


@dataclass(frozen=True, slots=True)
class UpdateOutput:
    """Parsed v1 ordered memory update proposal."""

    operations: Sequence[UpdateOperation] = field(repr=False)

    def __post_init__(self) -> None:
        operations = tuple(self.operations)
        if not operations or len(operations) > MAX_OUTPUT_ITEMS:
            raise DomainValidationError("operations must contain 1 to 100 values")
        if any(not isinstance(operation, UpdateOperation) for operation in operations):
            raise DomainValidationError("operations must contain UpdateOperation values")
        if (
            any(operation.event is MemoryEvent.NOOP for operation in operations)
            and len(operations) != 1
        ):
            raise DomainValidationError("noop must be the only update operation")
        object.__setattr__(self, "operations", operations)


type ParsedPromptOutput = SummaryOutput | FactOutput | ProfileOutput | UpdateOutput


SUMMARY_OUTPUT_SCHEMA = FrozenJsonObject(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "summary"],
        "properties": {
            "schema_version": {"const": OutputSchemaVersion.SUMMARY_V1.value},
            "summary": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_OUTPUT_TEXT_LENGTH,
            },
        },
    }
)

FACT_OUTPUT_SCHEMA = FrozenJsonObject(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "facts"],
        "properties": {
            "schema_version": {"const": OutputSchemaVersion.FACT_V1.value},
            "facts": {
                "type": "array",
                "maxItems": MAX_OUTPUT_ITEMS,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_OUTPUT_TEXT_LENGTH,
                },
            },
        },
    }
)

PROFILE_OUTPUT_SCHEMA = FrozenJsonObject(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "profile"],
        "properties": {
            "schema_version": {"const": OutputSchemaVersion.PROFILE_V1.value},
            "profile": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": MAX_OUTPUT_TEXT_LENGTH,
            },
        },
    }
)

UPDATE_OUTPUT_SCHEMA = FrozenJsonObject(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "operations"],
        "properties": {
            "schema_version": {"const": OutputSchemaVersion.UPDATE_V1.value},
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_OUTPUT_ITEMS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["event", "memory_id", "content"],
                    "properties": {
                        "event": {
                            "enum": [event.value for event in MemoryEvent],
                        },
                        "memory_id": {"type": ["string", "null"]},
                        "content": {
                            "type": ["string", "null"],
                            "maxLength": MAX_OUTPUT_TEXT_LENGTH,
                        },
                    },
                },
            },
        },
    }
)

_SCHEMAS = {
    OutputSchemaVersion.SUMMARY_V1: SUMMARY_OUTPUT_SCHEMA,
    OutputSchemaVersion.FACT_V1: FACT_OUTPUT_SCHEMA,
    OutputSchemaVersion.PROFILE_V1: PROFILE_OUTPUT_SCHEMA,
    OutputSchemaVersion.UPDATE_V1: UPDATE_OUTPUT_SCHEMA,
}


def output_schema(version: OutputSchemaVersion) -> FrozenJsonObject:
    """Return the immutable JSON schema for one supported output version."""

    if not isinstance(version, OutputSchemaVersion):
        raise DomainValidationError("version must be OutputSchemaVersion")
    return _SCHEMAS[version]
