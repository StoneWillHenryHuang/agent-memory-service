"""Lifecycle and in-flight operation coordination for the application service."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from portable_memory_engine.domain import LifecycleError
from portable_memory_engine.ports import AsyncLifecycle


class OperationLifecycle:
    """Open owned resources and prevent closure during active operations."""

    def __init__(self, resources: tuple[AsyncLifecycle, ...]) -> None:
        self._resources = resources
        self._lifecycle_lock = asyncio.Lock()
        self._activity = asyncio.Condition()
        self._open = False
        self._closing = False
        self._active_operations = 0

    async def open(self) -> None:
        """Open each owned resource once in declaration order."""

        async with self._lifecycle_lock:
            if self._open:
                return
            opened: list[AsyncLifecycle] = []
            try:
                for resource in self._resources:
                    await resource.open()
                    opened.append(resource)
            except BaseException:
                for resource in reversed(opened):
                    with suppress(BaseException):
                        await resource.close()
                raise
            async with self._activity:
                self._open = True
                self._closing = False

    async def close(self) -> None:
        """Wait for active work, then close owned resources in reverse order."""

        async with self._lifecycle_lock:
            async with self._activity:
                if not self._open:
                    return
                self._closing = True
                while self._active_operations:
                    await self._activity.wait()
                self._open = False
            close_error: BaseException | None = None
            for resource in reversed(self._resources):
                try:
                    await resource.close()
                except BaseException as error:
                    if close_error is None:
                        close_error = error
            async with self._activity:
                self._closing = False
            if close_error is not None:
                raise close_error

    async def begin_operation(self) -> None:
        """Claim one operation only while the service is open and accepting work."""

        async with self._activity:
            if not self._open or self._closing:
                raise LifecycleError("memory engine is not open")
            self._active_operations += 1

    async def end_operation(self) -> None:
        """Release one operation and wake a waiting close call."""

        async with self._activity:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._activity.notify_all()
