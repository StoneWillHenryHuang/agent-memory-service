"""Deterministic time and identifier ports."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class IdentifierPurpose(StrEnum):
    """Neutral purpose supplied when a new opaque identifier is requested."""

    MEMORY = "memory"
    COMMAND = "command"


@runtime_checkable
class Clock(Protocol):
    """Source of timezone-aware processing timestamps."""

    def now(self) -> datetime:
        """Return the current timezone-aware timestamp; callers normalize UTC."""

        ...


@runtime_checkable
class IdGenerator(Protocol):
    """Source of opaque identifiers with no database or UUID assumption."""

    def new_id(self, *, purpose: IdentifierPurpose) -> str:
        """Return a non-empty identifier for the requested neutral purpose."""

        ...
