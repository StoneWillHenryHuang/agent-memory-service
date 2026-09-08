"""Internal validation helpers shared by public domain value objects."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from portable_memory_engine.domain.enums import MemoryKind
from portable_memory_engine.domain.errors import DomainValidationError
from portable_memory_engine.domain.types import FrozenJsonObject, VersionToken

MAX_IDENTIFIER_LENGTH = 255
MAX_PAGE_SIZE = 1_000


def normalize_identifier(
    value: str,
    *,
    field_name: str,
    max_length: int = MAX_IDENTIFIER_LENGTH,
) -> str:
    """Normalize NFC and enforce the public identifier policy."""

    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or normalized != normalized.strip():
        raise DomainValidationError(f"{field_name} must be non-empty without outer whitespace")
    if len(normalized) > max_length:
        raise DomainValidationError(f"{field_name} exceeds its maximum length")
    if normalized == "*":
        raise DomainValidationError(f"{field_name} does not support wildcard values")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in normalized
    ):
        raise DomainValidationError(f"{field_name} contains a disallowed character")
    return normalized


def normalize_optional_identifier(
    value: str | None,
    *,
    field_name: str,
    max_length: int = MAX_IDENTIFIER_LENGTH,
) -> str | None:
    """Normalize an optional identifier when present."""

    if value is None:
        return None
    return normalize_identifier(value, field_name=field_name, max_length=max_length)


def normalize_content(value: str, *, field_name: str) -> str:
    """Normalize user-facing text without stripping meaningful whitespace."""

    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized.strip():
        raise DomainValidationError(f"{field_name} must not be empty")
    if "\x00" in normalized:
        raise DomainValidationError(f"{field_name} must not contain NUL")
    return normalized


def normalize_utc(value: datetime, *, field_name: str) -> datetime:
    """Require an aware datetime and normalize it to UTC."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def normalize_optional_utc(value: datetime | None, *, field_name: str) -> datetime | None:
    """Normalize an optional datetime to UTC."""

    if value is None:
        return None
    return normalize_utc(value, field_name=field_name)


def normalize_version(value: VersionToken | None, *, field_name: str) -> VersionToken | None:
    """Validate an opaque string or non-negative integer version."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise DomainValidationError(f"{field_name} must not be boolean")
    if isinstance(value, int):
        if value < 0:
            raise DomainValidationError(f"{field_name} must not be negative")
        return value
    if isinstance(value, str):
        return normalize_identifier(value, field_name=field_name)
    raise DomainValidationError(f"{field_name} must be a string, integer, or None")


def normalize_kinds(
    values: Iterable[MemoryKind], *, field_name: str = "kinds"
) -> frozenset[MemoryKind]:
    """Freeze and validate a collection of memory kinds."""

    kinds = frozenset(values)
    if any(not isinstance(kind, MemoryKind) for kind in kinds):
        raise DomainValidationError(f"{field_name} must contain MemoryKind values")
    return kinds


def normalize_metadata(value: Mapping[str, object]) -> FrozenJsonObject:
    """Deeply validate and freeze public metadata."""

    if not isinstance(value, Mapping):
        raise DomainValidationError("metadata must be a mapping")
    return FrozenJsonObject(value)


def validate_pagination(*, offset: int, limit: int) -> None:
    """Validate stable offset pagination inputs."""

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise DomainValidationError("offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise DomainValidationError(f"limit must be between 1 and {MAX_PAGE_SIZE}")


def normalize_score(value: float | None, *, field_name: str) -> float | None:
    """Validate a provider-neutral relevance score in the closed interval 0..1."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainValidationError(f"{field_name} must be numeric")
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise DomainValidationError(f"{field_name} must be between 0 and 1")
    return score
