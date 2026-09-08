"""Stable domain values with no provider or persistence dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from portable_memory_engine.domain.errors import DomainValidationError

_CUSTOM_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*\Z")
_DEFAULT_KIND_VALUES = frozenset({"summary", "fact", "profile"})


@dataclass(frozen=True, slots=True, order=True)
class MemoryKind:
    """An extensible memory kind with three stable default values."""

    value: str

    SUMMARY: ClassVar[MemoryKind]
    FACT: ClassVar[MemoryKind]
    PROFILE: ClassVar[MemoryKind]

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise DomainValidationError("memory kind must be a string")
        if self.value not in _DEFAULT_KIND_VALUES and not _CUSTOM_KIND_PATTERN.fullmatch(
            self.value
        ):
            raise DomainValidationError(
                "custom memory kinds must be namespaced lowercase values such as custom:episodic"
            )

    @property
    def is_default(self) -> bool:
        """Return whether this is one of the built-in v0.1 kinds."""

        return self.value in _DEFAULT_KIND_VALUES

    def __str__(self) -> str:
        """Return the stable serialized value."""

        return self.value


MemoryKind.SUMMARY = MemoryKind("summary")
MemoryKind.FACT = MemoryKind("fact")
MemoryKind.PROFILE = MemoryKind("profile")


class MemoryEvent(StrEnum):
    """A requested fact/update action returned by an extraction strategy."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"


class EmbeddingTask(StrEnum):
    """Provider-neutral purpose of an embedding request."""

    DOCUMENT = "document"
    QUERY = "query"


class SortOrder(StrEnum):
    """Stable recency ordering for memory queries."""

    NEWEST = "newest"
    OLDEST = "oldest"


class CountPrecision(StrEnum):
    """Meaning of a page's optional total count."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNAVAILABLE = "unavailable"


class PutStatus(StrEnum):
    """Successful outcome of one persisted record."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class DeleteTarget(StrEnum):
    """Supported deletion target."""

    MEMORY_ID = "memory_id"
    SCOPE = "scope"
    SUBJECT = "subject"
    SESSION = "session"
