"""Sanitized observability port with no telemetry SDK dependency."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from portable_memory_engine.domain import DomainValidationError, MemoryKind
from portable_memory_engine.ports.lifecycle import AsyncLifecycle


class ObservationOutcome(StrEnum):
    """Content-free outcome category emitted by application operations."""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ObservationEvent:
    """A bounded event that cannot carry memory, prompt, or provider content."""

    operation: str
    outcome: ObservationOutcome
    occurred_at: datetime
    duration_seconds: float | None = None
    kind: MemoryKind | None = None
    item_count: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation, str)
            or not self.operation
            or any(character.isspace() for character in self.operation)
        ):
            raise DomainValidationError("observation operation must be an identifier")
        if not isinstance(self.outcome, ObservationOutcome):
            raise DomainValidationError("observation outcome must be ObservationOutcome")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise DomainValidationError("observation occurred_at must be timezone-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        if self.duration_seconds is not None and (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds < 0
        ):
            raise DomainValidationError(
                "observation duration_seconds must be finite and non-negative"
            )
        if self.kind is not None and not isinstance(self.kind, MemoryKind):
            raise DomainValidationError("observation kind must be MemoryKind or None")
        if self.item_count is not None and (
            isinstance(self.item_count, bool)
            or not isinstance(self.item_count, int)
            or self.item_count < 0
        ):
            raise DomainValidationError("observation item_count must be a non-negative integer")


@runtime_checkable
class Observer(AsyncLifecycle, Protocol):
    """Sink for bounded events; telemetry SDK exceptions must be translated."""

    async def emit(self, event: ObservationEvent) -> None:
        """Record one sanitized event without changing domain behavior."""

        ...
