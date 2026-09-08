"""Record-level event-time checks before model and persistence work."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from portable_memory_engine.domain import MemoryKind, MemoryScope, StaleEventError
from portable_memory_engine.ports import MemoryStore


class FreshnessGuard:
    """Check effective store watermarks without weakening atomic store guards."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def ensure_current(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        kind: MemoryKind,
        event_timestamp: datetime,
    ) -> None:
        """Reject strictly older work; equal-time replay is decided atomically by the store."""

        watermark = await self._store.freshness_watermark(
            scope=scope,
            memory_id=memory_id,
            kind=kind,
        )
        if watermark is not None and event_timestamp < watermark:
            raise StaleEventError(
                event_timestamp=event_timestamp,
                watermark=watermark,
            )

    async def ensure_many_current(
        self,
        *,
        scope: MemoryScope,
        targets: Iterable[tuple[str, MemoryKind]],
        event_timestamp: datetime,
    ) -> None:
        """Check each unique logical target in deterministic order."""

        for memory_id, kind in sorted(set(targets), key=lambda value: (value[1].value, value[0])):
            await self.ensure_current(
                scope=scope,
                memory_id=memory_id,
                kind=kind,
                event_timestamp=event_timestamp,
            )
