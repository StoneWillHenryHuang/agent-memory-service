"""Synthetic, localhost-only FastAPI reference application."""

from __future__ import annotations

from datetime import UTC, datetime

from portable_memory_engine import (
    ChatResponse,
    DefaultPromptProvider,
    DeterministicEmbedder,
    FixedClock,
    InMemoryMemoryStore,
    MemoryEngine,
    ScriptedChatModel,
)
from portable_memory_engine.integrations.fastapi import create_reference_app

_EVENT_TIME = datetime(2026, 1, 1, tzinfo=UTC)

engine = MemoryEngine(
    store=InMemoryMemoryStore(),
    chat_model=ScriptedChatModel(
        (
            ChatResponse(
                content=(
                    '{"schema_version":"fact.v1","facts":'
                    '["The synthetic traveler prefers morning trains."]}'
                ),
                model_id="scripted-reference-v1",
            ),
            ChatResponse(
                content=(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"add","memory_id":null,"content":'
                    '"The synthetic traveler prefers morning trains."}]}'
                ),
                model_id="scripted-reference-v1",
            ),
        )
    ),
    embedder=DeterministicEmbedder(dimension=8),
    prompt_provider=DefaultPromptProvider(),
    clock=FixedClock(_EVENT_TIME),
)

app = create_reference_app(engine=engine)
