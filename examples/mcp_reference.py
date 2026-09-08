"""Synthetic stdio-only MCP reference server."""

from __future__ import annotations

import asyncio
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
from portable_memory_engine.integrations.mcp import (
    create_reference_mcp_server,
    run_reference_mcp_stdio,
)

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
                model_id="scripted-mcp-reference-v1",
            ),
            ChatResponse(
                content=(
                    '{"schema_version":"update.v1","operations":['
                    '{"event":"add","memory_id":null,"content":'
                    '"The synthetic traveler prefers morning trains."}]}'
                ),
                model_id="scripted-mcp-reference-v1",
            ),
        )
    ),
    embedder=DeterministicEmbedder(dimension=8),
    prompt_provider=DefaultPromptProvider(),
    clock=FixedClock(_EVENT_TIME),
)

server = create_reference_mcp_server(engine=engine)


if __name__ == "__main__":
    asyncio.run(run_reference_mcp_stdio(server))
