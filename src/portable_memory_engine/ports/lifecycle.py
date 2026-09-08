"""Lifecycle contract shared by resource-owning adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AsyncLifecycle(Protocol):
    """An explicitly opened and idempotently closed asynchronous resource.

    ``open`` and ``close`` must both be safe to call more than once. Operations
    outside the open interval raise ``LifecycleError``. Callers retain ownership
    unless a later facade explicitly documents that it manages the resource.
    """

    async def open(self) -> None:
        """Acquire resources without performing work for a domain operation."""

        ...

    async def close(self) -> None:
        """Release owned resources; repeated calls are no-ops."""

        ...
