"""Framework-neutral deletion application service."""

from __future__ import annotations

import time
from typing import cast

from portable_memory_engine.application.lifecycle import OperationLifecycle
from portable_memory_engine.application.support import (
    AllowAllAccessPolicy,
    NoopObserver,
    SystemClock,
)
from portable_memory_engine.domain import (
    AccessDeniedError,
    DeleteCommand,
    DeleteMemoryCommand,
    DeleteResult,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    DomainValidationError,
    MemoryKind,
    MemoryScope,
    MemorySubject,
)
from portable_memory_engine.ports import (
    AccessOperation,
    AccessPolicy,
    AccessRequest,
    AsyncLifecycle,
    Clock,
    MemoryStore,
    ObservationEvent,
    ObservationOutcome,
    Observer,
    StorageCapability,
)

_COMMAND_TYPES = (
    DeleteMemoryCommand,
    DeleteScopeCommand,
    DeleteSubjectCommand,
    DeleteSessionCommand,
)


class DeleteEngine:
    """Authorize, execute, and safely observe one typed deletion command."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        access_policy: AccessPolicy | None = None,
        observer: Observer | None = None,
        clock: Clock | None = None,
        owns_resources: bool = True,
    ) -> None:
        if not isinstance(owns_resources, bool):
            raise DomainValidationError("owns_resources must be boolean")
        self._store = store
        self._access_policy = access_policy or AllowAllAccessPolicy()
        default_observer = observer is None
        self._observer = observer or NoopObserver()
        self._clock = clock or SystemClock()

        resources = cast(
            "tuple[AsyncLifecycle, ...]",
            (store, self._observer),
        )
        unique: list[AsyncLifecycle] = []
        seen: set[int] = set()
        for resource in resources:
            if id(resource) not in seen:
                seen.add(id(resource))
                unique.append(resource)
        managed_resources = (
            tuple(unique)
            if owns_resources
            else (cast("AsyncLifecycle", self._observer),)
            if default_observer
            else ()
        )
        self._lifecycle = OperationLifecycle(managed_resources)

    async def __aenter__(self) -> DeleteEngine:
        """Open owned resources and return the deletion service."""

        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        """Close owned resources after active deletion calls complete."""

        await self.close()

    async def open(self) -> None:
        """Open owned lifecycle dependencies."""

        await self._lifecycle.open()

    async def close(self) -> None:
        """Wait for active deletions and close owned dependencies."""

        await self._lifecycle.close()

    @staticmethod
    def _boundary(command: DeleteCommand) -> MemoryScope | MemorySubject:
        return command.subject if isinstance(command, DeleteSubjectCommand) else command.scope

    @staticmethod
    def _kind(command: DeleteCommand) -> MemoryKind | None:
        if isinstance(command, (DeleteScopeCommand, DeleteSubjectCommand)):
            return next(iter(command.kinds)) if len(command.kinds) == 1 else None
        return None

    def _require_capabilities(self, command: DeleteCommand) -> None:
        required = [
            StorageCapability.ATOMIC_IDEMPOTENCY,
            StorageCapability.DELETION_BARRIERS,
        ]
        if isinstance(command, DeleteSubjectCommand):
            required.append(StorageCapability.SUBJECT_DELETION)
        if isinstance(command, DeleteSessionCommand):
            required.append(StorageCapability.SESSION_CONTRIBUTIONS)
        self._store.capabilities.require(
            operation=f"delete_{command.target.value}",
            capabilities=tuple(required),
        )

    async def _emit(
        self,
        *,
        command: DeleteCommand,
        outcome: ObservationOutcome,
        started: float,
        item_count: int,
    ) -> None:
        await self._observer.emit(
            ObservationEvent(
                operation=f"memory.delete.{command.target.value}",
                outcome=outcome,
                occurred_at=self._clock.now(),
                duration_seconds=time.perf_counter() - started,
                kind=self._kind(command),
                item_count=item_count,
            )
        )

    async def delete(self, command: DeleteCommand) -> DeleteResult:
        """Apply one authorized delete without transport-specific outcomes."""

        if not isinstance(command, _COMMAND_TYPES):
            raise DomainValidationError("delete requires a typed deletion command")
        await self._lifecycle.begin_operation()
        started = time.perf_counter()
        try:
            self._require_capabilities(command)
            decision = await self._access_policy.authorize(
                AccessRequest(self._boundary(command), AccessOperation.DELETE)
            )
            if not decision.allowed:
                raise AccessDeniedError(operation="memory.delete")
            result = await self._store.delete(command)
            await self._emit(
                command=command,
                outcome=ObservationOutcome.SUCCEEDED,
                started=started,
                item_count=result.deleted_count,
            )
            return result
        except AccessDeniedError:
            await self._emit(
                command=command,
                outcome=ObservationOutcome.REJECTED,
                started=started,
                item_count=0,
            )
            raise
        except Exception:
            await self._emit(
                command=command,
                outcome=ObservationOutcome.FAILED,
                started=started,
                item_count=0,
            )
            raise
        finally:
            await self._lifecycle.end_operation()
