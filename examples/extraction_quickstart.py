"""Complete network-free extraction with a caller-supplied scripted model."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from portable_memory_engine.adapters import (
    DeterministicEmbedder,
    FixedClock,
    InMemoryMemoryStore,
)
from portable_memory_engine.application import AddMemoryCommand, AddResultStatus, MemoryEngine
from portable_memory_engine.domain import ConversationMessage, MemoryKind, MemoryQuery, MemoryScope
from portable_memory_engine.ports import ChatRequest, ChatResponse
from portable_memory_engine.prompts import DefaultPromptProvider


class ScriptedChatModel:
    """Small example double; real applications inject their own model adapter."""

    def __init__(self, responses: tuple[str, ...]) -> None:
        self._responses = responses
        self._index = 0
        self._open = False

    async def open(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False

    async def complete(self, request: ChatRequest) -> ChatResponse:
        assert self._open
        assert request.response_schema
        content = self._responses[self._index]
        self._index += 1
        return ChatResponse(content=content, model_id="scripted-example-v1")


async def main() -> None:
    """Extract all built-in kinds without credentials or network I/O."""

    responses = (
        '{"schema_version":"summary.v1","summary":"A traveler is planning a rail trip"}',
        '{"schema_version":"fact.v1","facts":["The traveler prefers morning trains"]}',
        '{"schema_version":"update.v1","operations":['
        '{"event":"add","memory_id":null,'
        '"content":"The traveler prefers morning trains"}]}',
        '{"schema_version":"profile.v1","profile":"Prefers early rail travel"}',
    )
    scope = MemoryScope(subject_id="example-subject", namespace="quickstart")
    store = InMemoryMemoryStore()
    engine = MemoryEngine(
        store=store,
        chat_model=ScriptedChatModel(responses),
        embedder=DeterministicEmbedder(dimension=8),
        prompt_provider=DefaultPromptProvider(),
        clock=FixedClock(datetime(2026, 1, 1, 1, tzinfo=UTC)),
    )
    async with engine:
        result = await engine.add(
            AddMemoryCommand(
                scope=scope,
                messages=(
                    ConversationMessage(
                        role="user",
                        content="I prefer morning trains for this trip.",
                    ),
                ),
                idempotency_key="extraction-example-command-1",
                event_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="example-session-1",
                source="example",
            )
        )
        page = await store.query(MemoryQuery(scope=scope, limit=10))

    assert result.status is AddResultStatus.SUCCEEDED
    assert {match.record.kind for match in page.items} == {
        MemoryKind.SUMMARY,
        MemoryKind.FACT,
        MemoryKind.PROFILE,
    }


if __name__ == "__main__":
    asyncio.run(main())
