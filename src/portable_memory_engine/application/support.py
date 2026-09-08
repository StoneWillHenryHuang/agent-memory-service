"""Dependency-free defaults for policy, time, and content-free observation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from portable_memory_engine.domain import DomainValidationError, LifecycleError
from portable_memory_engine.ports import (
    AccessDecision,
    AccessRequest,
    ObservationEvent,
)


class AllowAllAccessPolicy:
    """Explicit single-process default that approves every already-scoped request."""

    async def authorize(self, request: AccessRequest) -> AccessDecision:
        """Allow a valid scoped request without removing store-side isolation."""

        if not isinstance(request, AccessRequest):
            raise DomainValidationError("access request must be AccessRequest")
        return AccessDecision(allowed=True)


class SystemClock:
    """Standard-library aware-UTC processing clock."""

    def now(self) -> datetime:
        """Return current processing time in UTC."""

        return datetime.now(UTC)


class NoopObserver:
    """Lifecycle-aware observer that deliberately records no events."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._open = False

    async def open(self) -> None:
        """Open the no-op observer."""

        async with self._lock:
            self._open = True

    async def close(self) -> None:
        """Close the no-op observer; repeated calls are safe."""

        async with self._lock:
            self._open = False

    async def emit(self, event: ObservationEvent) -> None:
        """Validate lifecycle and discard one already-sanitized event."""

        if not isinstance(event, ObservationEvent):
            raise DomainValidationError("observer event must be ObservationEvent")
        async with self._lock:
            if not self._open:
                raise LifecycleError("no-op observer is not open")
