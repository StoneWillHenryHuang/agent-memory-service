"""Resolve a generic prompt and parse one synthetic provider response."""

from __future__ import annotations

import asyncio

from portable_memory_engine.domain import MemoryKind
from portable_memory_engine.ports import PromptPurpose, PromptRequest
from portable_memory_engine.prompts import (
    DefaultPromptProvider,
    FactOutput,
    OutputSchemaVersion,
    parse_prompt_output,
)


async def main() -> None:
    provider = DefaultPromptProvider()
    await provider.open()
    template = await provider.get_prompt(PromptRequest(PromptPurpose.EXTRACTION, MemoryKind.FACT))
    rendered_user_text = template.user_text.format(
        conversation="A traveler says they prefer morning trains."
    )
    await provider.close()

    synthetic_response = (
        '{"schema_version":"fact.v1","facts":["The traveler prefers morning trains"]}'
    )
    parsed = parse_prompt_output(
        synthetic_response,
        schema_version=OutputSchemaVersion.FACT_V1,
    )

    assert isinstance(parsed, FactOutput)
    assert rendered_user_text
    assert parsed.facts == ("The traveler prefers morning trains",)


if __name__ == "__main__":
    asyncio.run(main())
