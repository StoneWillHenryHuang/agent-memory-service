"""Copyable, network-free use of the stable package-root facade."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from portable_memory_engine import (
    AddMemoryCommand,
    AddResultStatus,
    ChatResponse,
    ConversationMessage,
    DefaultPromptProvider,
    DeterministicEmbedder,
    FixedClock,
    InMemoryMemoryStore,
    MemoryEngine,
    MemoryKind,
    MemoryQuery,
    MemoryScope,
    RecallRequest,
    ScriptedChatModel,
)


async def main() -> None:
    """Add and recall one synthetic fact without credentials or network I/O."""

    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    scope = MemoryScope(subject_id="synthetic-traveler", namespace="quickstart")
    engine = MemoryEngine(
        store=InMemoryMemoryStore(),
        chat_model=ScriptedChatModel(
            (
                ChatResponse(
                    content=(
                        '{"schema_version":"fact.v1","facts":'
                        '["The synthetic traveler prefers morning trains."]}'
                    ),
                    model_id="scripted-quickstart-v1",
                ),
                ChatResponse(
                    content=(
                        '{"schema_version":"update.v1","operations":['
                        '{"event":"add","memory_id":null,"content":'
                        '"The synthetic traveler prefers morning trains."}]}'
                    ),
                    model_id="scripted-quickstart-v1",
                ),
            )
        ),
        embedder=DeterministicEmbedder(dimension=8),
        prompt_provider=DefaultPromptProvider(),
        clock=FixedClock(event_time),
    )

    async with engine:
        added = await engine.add(
            AddMemoryCommand(
                scope=scope,
                messages=(
                    ConversationMessage(
                        role="user",
                        content="I prefer morning trains for this synthetic trip.",
                    ),
                ),
                idempotency_key="quickstart-add-1",
                event_timestamp=event_time,
                kinds=frozenset({MemoryKind.FACT}),
                source="quickstart",
                session_id="synthetic-session-1",
            )
        )
        recalled = await engine.recall(RecallRequest(MemoryQuery(scope=scope)))

    assert added.status is AddResultStatus.SUCCEEDED
    assert [match.record.content for match in recalled.items] == [
        "The synthetic traveler prefers morning trains."
    ]


if __name__ == "__main__":
    asyncio.run(main())
