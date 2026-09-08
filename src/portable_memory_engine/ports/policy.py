"""Framework-neutral scoped access-policy port."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from portable_memory_engine.domain import DomainValidationError, MemoryScope, MemorySubject

type AccessBoundary = MemoryScope | MemorySubject


class AccessOperation(StrEnum):
    """Stable operation categories understood by an access policy."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """A scope or explicit subject-wide authorization request."""

    scope: AccessBoundary
    operation: AccessOperation

    def __post_init__(self) -> None:
        if not isinstance(self.scope, (MemoryScope, MemorySubject)):
            raise DomainValidationError("access request scope must be MemoryScope or MemorySubject")
        if not isinstance(self.operation, AccessOperation):
            raise DomainValidationError("access request operation must be AccessOperation")


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """A sanitized allow/deny decision; reason codes must contain no user data."""

    allowed: bool
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise DomainValidationError("access decision allowed must be boolean")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str)
            or not self.reason_code
            or self.reason_code != self.reason_code.strip()
            or any(character.isspace() for character in self.reason_code)
        ):
            raise DomainValidationError("access decision reason_code must be an identifier")
        if self.allowed and self.reason_code is not None:
            raise DomainValidationError("allowed access decisions must not carry a denial reason")
        if not self.allowed and self.reason_code is None:
            raise DomainValidationError("denied access decisions require a reason code")


@runtime_checkable
class AccessPolicy(Protocol):
    """Asynchronous policy boundary that never returns HTTP-specific values."""

    async def authorize(self, request: AccessRequest) -> AccessDecision:
        """Return a bounded decision; storage isolation remains mandatory."""

        ...
