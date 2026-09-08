"""Synthetic application test doubles and factories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from portable_memory_engine.adapters import (
    DeterministicEmbedder,
    FixedClock,
    InMemoryMemoryStore,
)
from portable_memory_engine.application import (
    AddMemoryCommand,
    ExtractionLimits,
    MemoryEngine,
)
from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    LifecycleError,
    MemoryKind,
    MemoryScope,
    ProviderError,
)
from portable_memory_engine.ports import (
    AccessDecision,
    AccessRequest,
    ChatRequest,
    ChatResponse,
    ObservationEvent,
)
from portable_memory_engine.prompts import DefaultPromptProvider

EVENT_TIME = datetime(2026, 7, 31, 8, tzinfo=UTC)
PROCESSING_TIME = datetime(2026, 7, 31, 9, tzinfo=UTC)
SCOPE = MemoryScope(subject_id="synthetic-subject", namespace="synthetic-app")


class RecordingChatModel:
    """Lifecycle test model that records only synthetic requests."""

    def __init__(
        self,
        script: Sequence[ChatResponse | ProviderError],
        *,
        hook: Callable[[ChatRequest, int], Awaitable[None]] | None = None,
    ) -> None:
        self._script = tuple(script)
        self._hook = hook
        self._open = False
        self._index = 0
        self.requests: list[ChatRequest] = []

    @property
    def call_count(self) -> int:
        return self._index

    async def open(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if not self._open:
            raise LifecycleError("recording chat model is not open")
        if self._index >= len(self._script):
            raise ProviderError("recording chat model has no remaining response")
        index = self._index
        self._index += 1
        self.requests.append(request)
        if self._hook is not None:
            await self._hook(request, index)
        step = self._script[index]
        if isinstance(step, ProviderError):
            raise step from None
        return step


class RecordingObserver:
    """Content-free lifecycle observer used only by synthetic tests."""

    def __init__(self) -> None:
        self.events: list[ObservationEvent] = []
        self.is_open = False

    async def open(self) -> None:
        self.is_open = True

    async def close(self) -> None:
        self.is_open = False

    async def emit(self, event: ObservationEvent) -> None:
        if not self.is_open:
            raise LifecycleError("recording observer is not open")
        self.events.append(event)


class DenyAccessPolicy:
    """Synthetic policy denying every scoped operation."""

    async def authorize(self, request: AccessRequest) -> AccessDecision:
        if not isinstance(request, AccessRequest):
            raise DomainValidationError("expected AccessRequest")
        return AccessDecision(allowed=False, reason_code="synthetic_denial")


def response(content: str, *, model_id: str = "synthetic-model-v1") -> ChatResponse:
    return ChatResponse(content=content, model_id=model_id)


def command(
    *,
    kinds: frozenset[MemoryKind] | None = None,
    key: str = "synthetic-command-1",
    event_timestamp: datetime = EVENT_TIME,
    messages: Sequence[ConversationMessage] | None = None,
) -> AddMemoryCommand:
    return AddMemoryCommand(
        scope=SCOPE,
        messages=(
            tuple(messages)
            if messages is not None
            else (
                ConversationMessage(role="assistant", content="Synthetic assistant context"),
                ConversationMessage(role="user", content="I prefer morning trains"),
            )
        ),
        idempotency_key=key,
        event_timestamp=event_timestamp,
        kinds=(
            kinds
            if kinds is not None
            else frozenset((MemoryKind.SUMMARY, MemoryKind.FACT, MemoryKind.PROFILE))
        ),
        source="synthetic-chat",
        session_id="synthetic-session-1",
    )


def build_engine(
    script: Sequence[ChatResponse | ProviderError],
    *,
    store: InMemoryMemoryStore | None = None,
    model: RecordingChatModel | None = None,
    observer: RecordingObserver | None = None,
    limits: ExtractionLimits | None = None,
    access_policy: DenyAccessPolicy | None = None,
    owns_resources: bool = True,
) -> tuple[
    MemoryEngine,
    InMemoryMemoryStore,
    RecordingChatModel,
    RecordingObserver,
    DeterministicEmbedder,
]:
    selected_store = store or InMemoryMemoryStore()
    selected_model = model or RecordingChatModel(script)
    selected_observer = observer or RecordingObserver()
    embedder = DeterministicEmbedder(dimension=8, model_id="synthetic-embedding-v1")
    engine = MemoryEngine(
        store=selected_store,
        chat_model=selected_model,
        embedder=embedder,
        prompt_provider=DefaultPromptProvider(),
        observer=selected_observer,
        clock=FixedClock(PROCESSING_TIME),
        access_policy=access_policy,
        limits=limits,
        owns_resources=owns_resources,
    )
    return engine, selected_store, selected_model, selected_observer, embedder
