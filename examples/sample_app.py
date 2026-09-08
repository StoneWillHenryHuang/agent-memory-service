"""Offline synthetic application using the stable add, recall, and delete facade."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from portable_memory_engine import (
    AddMemoryCommand,
    AddResultStatus,
    ChatResponse,
    ConversationMessage,
    DefaultPromptProvider,
    DeleteMemoryCommand,
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

EVENT_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SYNTHETIC_MEMORY = "The synthetic traveler prefers morning trains."


@dataclass(frozen=True, slots=True)
class SampleResult:
    """Small content-bounded result returned by the synthetic workflow."""

    add_status: AddResultStatus
    recalled_contents: tuple[str, ...]
    deleted_count: int
    remaining_count: int


def build_engine() -> MemoryEngine:
    """Build a deterministic engine with no credentials, files, or network."""

    return MemoryEngine(
        store=InMemoryMemoryStore(),
        chat_model=ScriptedChatModel(
            (
                ChatResponse(
                    content=(f'{{"schema_version":"fact.v1","facts":["{SYNTHETIC_MEMORY}"]}}'),
                    model_id="scripted-sample-app-v1",
                ),
                ChatResponse(
                    content=(
                        '{"schema_version":"update.v1","operations":['
                        '{"event":"add","memory_id":null,"content":'
                        f'"{SYNTHETIC_MEMORY}"}}]}}'
                    ),
                    model_id="scripted-sample-app-v1",
                ),
            )
        ),
        embedder=DeterministicEmbedder(dimension=8),
        prompt_provider=DefaultPromptProvider(),
        clock=FixedClock(EVENT_TIME),
    )


async def run_sample() -> SampleResult:
    """Remember, recall, and delete one entirely synthetic preference."""

    scope = MemoryScope(
        tenant_id="synthetic-tenant",
        subject_id="synthetic-traveler",
        namespace="sample-app",
    )
    engine = build_engine()

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
                idempotency_key="sample-app-add-1",
                event_timestamp=EVENT_TIME,
                kinds=frozenset({MemoryKind.FACT}),
                source="sample-app",
                session_id="synthetic-session-1",
            )
        )
        recalled = await engine.recall(RecallRequest(MemoryQuery(scope=scope)))
        memory_id = recalled.items[0].record.id
        deleted = await engine.delete(
            DeleteMemoryCommand(
                scope=scope,
                memory_id=memory_id,
                idempotency_key="sample-app-delete-1",
                payload_digest="sample-app-delete-digest-1",
                event_timestamp=EVENT_TIME + timedelta(minutes=1),
            )
        )
        remaining = await engine.recall(RecallRequest(MemoryQuery(scope=scope)))

    return SampleResult(
        add_status=added.status,
        recalled_contents=tuple(match.record.content for match in recalled.items),
        deleted_count=deleted.deleted_count,
        remaining_count=len(remaining.items),
    )


async def main() -> None:
    """Run a self-checking workflow and exit quietly on success."""

    result = await run_sample()
    assert result == SampleResult(
        add_status=AddResultStatus.SUCCEEDED,
        recalled_contents=(SYNTHETIC_MEMORY,),
        deleted_count=1,
        remaining_count=0,
    )


if __name__ == "__main__":
    asyncio.run(main())
