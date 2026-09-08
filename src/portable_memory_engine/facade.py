"""Stable public assembly facade for add, recall, and deletion use cases."""

from __future__ import annotations

from typing import cast

from portable_memory_engine.application.deletion import DeleteEngine
from portable_memory_engine.application.engine import MemoryEngine as AddMemoryEngine
from portable_memory_engine.application.extraction import (
    FactExtractionStrategy,
    ProfileExtractionStrategy,
    SummaryExtractionStrategy,
)
from portable_memory_engine.application.identity import IdentityStrategy
from portable_memory_engine.application.lifecycle import OperationLifecycle
from portable_memory_engine.application.messages import UserMessageFilter
from portable_memory_engine.application.models import (
    AddMemoryCommand,
    AddMemoryResult,
    ExtractionLimits,
)
from portable_memory_engine.application.recall import RecallEngine, RecallLimits, RecallResult
from portable_memory_engine.application.rerank import RerankStrategy
from portable_memory_engine.application.selection import RecallRequest, RecallSelectionStrategy
from portable_memory_engine.application.support import (
    AllowAllAccessPolicy,
    NoopObserver,
    SystemClock,
)
from portable_memory_engine.application.update import MemoryUpdateStrategy
from portable_memory_engine.domain import DeleteCommand, DeleteResult, DomainValidationError
from portable_memory_engine.ports import (
    AccessPolicy,
    AsyncLifecycle,
    ChatModel,
    Clock,
    Embedder,
    MemoryStore,
    Observer,
    PromptProvider,
)


class MemoryEngine:
    """Assemble and own the public add, recall, and deletion services.

    The caller chooses every external dependency. By default the facade owns
    their async lifecycle; set ``owns_resources=False`` only when another
    application component opens and closes all injected resources.
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        chat_model: ChatModel,
        embedder: Embedder,
        prompt_provider: PromptProvider,
        access_policy: AccessPolicy | None = None,
        observer: Observer | None = None,
        clock: Clock | None = None,
        identity_strategy: IdentityStrategy | None = None,
        user_message_filter: UserMessageFilter | None = None,
        summary_extractor: SummaryExtractionStrategy | None = None,
        fact_extractor: FactExtractionStrategy | None = None,
        profile_extractor: ProfileExtractionStrategy | None = None,
        memory_updater: MemoryUpdateStrategy | None = None,
        extraction_limits: ExtractionLimits | None = None,
        recall_selector: RecallSelectionStrategy | None = None,
        reranker: RerankStrategy | None = None,
        recall_limits: RecallLimits | None = None,
        owns_resources: bool = True,
    ) -> None:
        if not isinstance(owns_resources, bool):
            raise DomainValidationError("owns_resources must be boolean")

        selected_policy = access_policy or AllowAllAccessPolicy()
        default_observer = observer is None
        selected_observer = observer or NoopObserver()
        selected_clock = clock or SystemClock()

        self._add_engine = AddMemoryEngine(
            store=store,
            chat_model=chat_model,
            embedder=embedder,
            prompt_provider=prompt_provider,
            access_policy=selected_policy,
            observer=selected_observer,
            clock=selected_clock,
            identity_strategy=identity_strategy,
            user_message_filter=user_message_filter,
            summary_extractor=summary_extractor,
            fact_extractor=fact_extractor,
            profile_extractor=profile_extractor,
            memory_updater=memory_updater,
            limits=extraction_limits,
            owns_resources=False,
        )
        self._recall_engine = RecallEngine(
            store=store,
            embedder=embedder,
            access_policy=selected_policy,
            observer=selected_observer,
            clock=selected_clock,
            selector=recall_selector,
            reranker=reranker,
            limits=recall_limits,
            owns_resources=False,
        )
        self._delete_engine = DeleteEngine(
            store=store,
            access_policy=selected_policy,
            observer=selected_observer,
            clock=selected_clock,
            owns_resources=False,
        )

        resources = cast(
            "tuple[AsyncLifecycle, ...]",
            (store, chat_model, embedder, prompt_provider, selected_observer),
        )
        unique: list[AsyncLifecycle] = []
        seen: set[int] = set()
        for resource in resources:
            if id(resource) not in seen:
                seen.add(id(resource))
                unique.append(resource)
        owned_resources = (
            tuple(unique)
            if owns_resources
            else (cast("AsyncLifecycle", selected_observer),)
            if default_observer
            else ()
        )
        child_services = cast(
            "tuple[AsyncLifecycle, ...]",
            (self._add_engine, self._recall_engine, self._delete_engine),
        )
        self._lifecycle = OperationLifecycle((*owned_resources, *child_services))

    async def __aenter__(self) -> MemoryEngine:
        """Open the facade and all resources it owns."""

        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        """Close the facade after active operations finish."""

        await self.close()

    async def open(self) -> None:
        """Open owned resources once in dependency order."""

        await self._lifecycle.open()

    async def close(self) -> None:
        """Wait for active work and close owned resources in reverse order."""

        await self._lifecycle.close()

    async def add(self, command: AddMemoryCommand) -> AddMemoryResult:
        """Extract and persist one scoped, idempotent memory command."""

        await self._lifecycle.begin_operation()
        try:
            return await self._add_engine.add(command)
        finally:
            await self._lifecycle.end_operation()

    async def recall(self, request: RecallRequest) -> RecallResult:
        """Run one scoped recency or semantic recall."""

        await self._lifecycle.begin_operation()
        try:
            return await self._recall_engine.recall(request)
        finally:
            await self._lifecycle.end_operation()

    async def delete(self, command: DeleteCommand) -> DeleteResult:
        """Apply one scoped typed deletion command."""

        await self._lifecycle.begin_operation()
        try:
            return await self._delete_engine.delete(command)
        finally:
            await self._lifecycle.end_operation()
