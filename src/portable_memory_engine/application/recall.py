"""Async recall application service with scoped selection and safe observation."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from portable_memory_engine.application.lifecycle import OperationLifecycle
from portable_memory_engine.application.rerank import (
    NoOpReranker,
    RerankRequest,
    RerankStrategy,
)
from portable_memory_engine.application.selection import (
    RecallMode,
    RecallRequest,
    RecallSelectionStrategy,
    StoreRecallSelector,
)
from portable_memory_engine.application.support import (
    AllowAllAccessPolicy,
    NoopObserver,
    SystemClock,
)
from portable_memory_engine.domain import (
    AccessDeniedError,
    DomainValidationError,
    MemoryMatch,
    MemoryPage,
)
from portable_memory_engine.ports import (
    AccessOperation,
    AccessPolicy,
    AccessRequest,
    AsyncLifecycle,
    Clock,
    Embedder,
    MemoryStore,
    ObservationEvent,
    ObservationOutcome,
    Observer,
)


@dataclass(frozen=True, slots=True)
class RecallLimits:
    """Application bounds checked before an embedding provider is invoked."""

    max_semantic_query_characters: int = 10_000

    def __post_init__(self) -> None:
        value = self.max_semantic_query_characters
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise DomainValidationError("max_semantic_query_characters must be a positive integer")


@dataclass(frozen=True, slots=True)
class RecallResult:
    """Structured recall result retaining the store's pagination precision."""

    mode: RecallMode
    page: MemoryPage = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.mode, RecallMode):
            raise DomainValidationError("recall result mode must be RecallMode")
        if not isinstance(self.page, MemoryPage):
            raise DomainValidationError("recall result page must be MemoryPage")

    @property
    def items(self) -> tuple[MemoryMatch, ...]:
        """Return the immutable recalled matches."""

        return tuple(self.page.items)


class RecallEngine:
    """Authorize, select, optionally rerank, and observe one scoped recall."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        embedder: Embedder | None = None,
        access_policy: AccessPolicy | None = None,
        observer: Observer | None = None,
        clock: Clock | None = None,
        selector: RecallSelectionStrategy | None = None,
        reranker: RerankStrategy | None = None,
        limits: RecallLimits | None = None,
        owns_resources: bool = True,
    ) -> None:
        selected_limits = limits or RecallLimits()
        if not isinstance(selected_limits, RecallLimits):
            raise DomainValidationError("recall limits must be RecallLimits")
        if not isinstance(owns_resources, bool):
            raise DomainValidationError("owns_resources must be boolean")
        selected_selector = selector or StoreRecallSelector(store=store, embedder=embedder)
        if not isinstance(selected_selector, RecallSelectionStrategy):
            raise DomainValidationError("selector must implement RecallSelectionStrategy")
        selected_reranker = reranker or NoOpReranker()
        if not isinstance(selected_reranker, RerankStrategy):
            raise DomainValidationError("reranker must implement RerankStrategy")

        self._access_policy = access_policy or AllowAllAccessPolicy()
        default_observer = observer is None
        self._observer = observer or NoopObserver()
        self._clock = clock or SystemClock()
        self._selector = selected_selector
        self._reranker = selected_reranker
        self._limits = selected_limits

        resources = cast(
            "tuple[AsyncLifecycle, ...]",
            tuple(
                resource for resource in (store, embedder, self._observer) if resource is not None
            ),
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

    async def __aenter__(self) -> RecallEngine:
        """Open owned resources and return the recall service."""

        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        """Close owned resources after active recall calls complete."""

        await self.close()

    async def open(self) -> None:
        """Open owned lifecycle dependencies."""

        await self._lifecycle.open()

    async def close(self) -> None:
        """Wait for active recall work and close owned dependencies."""

        await self._lifecycle.close()

    async def _emit(
        self,
        *,
        request: RecallRequest,
        outcome: ObservationOutcome,
        started: float,
        item_count: int,
    ) -> None:
        kinds = request.query.kinds
        kind = next(iter(kinds)) if len(kinds) == 1 else None
        await self._observer.emit(
            ObservationEvent(
                operation=f"memory.recall.{request.mode.value}",
                outcome=outcome,
                occurred_at=self._clock.now(),
                duration_seconds=time.perf_counter() - started,
                kind=kind,
                item_count=item_count,
            )
        )

    @staticmethod
    def _page_after_rerank(
        page: MemoryPage,
        reranked: Sequence[MemoryMatch],
    ) -> MemoryPage:
        matches = tuple(reranked)
        if any(not isinstance(match, MemoryMatch) for match in matches):
            raise DomainValidationError("reranker returned a non-MemoryMatch value")
        selected = {match.record.id: match.record for match in page.items}
        output_ids = [match.record.id for match in matches]
        if len(output_ids) != len(set(output_ids)):
            raise DomainValidationError("reranker returned duplicate memory identities")
        if any(
            match.record.id not in selected or selected[match.record.id] != match.record
            for match in matches
        ):
            raise DomainValidationError("reranker returned a record outside the selected page")
        return MemoryPage(
            scope=page.scope,
            items=matches,
            offset=page.offset,
            limit=page.limit,
            next_offset=page.next_offset,
            total_count=page.total_count,
            count_precision=page.count_precision,
        )

    async def recall(self, request: RecallRequest) -> RecallResult:
        """Run one authorized recall without exposing query or memory content."""

        if not isinstance(request, RecallRequest):
            raise DomainValidationError("recall requires RecallRequest")
        await self._lifecycle.begin_operation()
        started = time.perf_counter()
        try:
            if (
                request.semantic_text is not None
                and len(request.semantic_text) > self._limits.max_semantic_query_characters
            ):
                raise DomainValidationError("semantic query exceeds the recall limit")
            decision = await self._access_policy.authorize(
                AccessRequest(request.query.scope, AccessOperation.READ)
            )
            if not decision.allowed:
                raise AccessDeniedError(operation="memory.recall")
            selected = await self._selector.select(request)
            if selected.scope != request.query.scope:
                raise DomainValidationError("selector returned a page outside the query scope")
            reranked = await self._reranker.rerank(
                RerankRequest(recall=request, candidates=selected.items)
            )
            page = self._page_after_rerank(selected, reranked)
            result = RecallResult(request.mode, page)
            await self._emit(
                request=request,
                outcome=ObservationOutcome.SUCCEEDED,
                started=started,
                item_count=len(page.items),
            )
            return result
        except AccessDeniedError:
            await self._emit(
                request=request,
                outcome=ObservationOutcome.REJECTED,
                started=started,
                item_count=0,
            )
            raise
        except Exception:
            await self._emit(
                request=request,
                outcome=ObservationOutcome.FAILED,
                started=started,
                item_count=0,
            )
            raise
        finally:
            await self._lifecycle.end_operation()
