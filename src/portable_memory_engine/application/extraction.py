"""Small model-facing strategies for summary, fact, and profile extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from portable_memory_engine.application.messages import (
    UserMessageFilter,
    message_values,
    render_json,
)
from portable_memory_engine.application.model import PromptCallResult, PromptRunner
from portable_memory_engine.domain import ConversationMessage, MemoryKind, MemoryRecord
from portable_memory_engine.ports import (
    PromptPurpose,
    PromptRequest,
    PromptTemplate,
)
from portable_memory_engine.prompts import (
    FactOutput,
    OutputSchemaVersion,
    ProfileOutput,
    SummaryOutput,
)


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """Bounded input shared by built-in model extraction strategies."""

    messages: tuple[ConversationMessage, ...] = field(repr=False)
    existing: MemoryRecord | None = field(default=None, repr=False)
    locale: str = "en"
    source: str | None = field(default=None, repr=False)
    override: PromptTemplate | None = field(default=None, repr=False)


@runtime_checkable
class SummaryExtractionStrategy(Protocol):
    """Replaceable strategy producing one parsed summary candidate."""

    async def extract(self, context: ExtractionContext) -> PromptCallResult[SummaryOutput]: ...


@runtime_checkable
class FactExtractionStrategy(Protocol):
    """Replaceable strategy producing zero or more parsed fact candidates."""

    async def extract(self, context: ExtractionContext) -> PromptCallResult[FactOutput] | None: ...


@runtime_checkable
class ProfileExtractionStrategy(Protocol):
    """Replaceable strategy producing one optional profile candidate."""

    async def extract(self, context: ExtractionContext) -> PromptCallResult[ProfileOutput]: ...


class SummaryExtractor:
    """Default summary model strategy with optional prior-summary context."""

    def __init__(self, runner: PromptRunner) -> None:
        self._runner = runner

    async def extract(self, context: ExtractionContext) -> PromptCallResult[SummaryOutput]:
        """Generate and strictly parse one summary candidate."""

        conversation = render_json(
            {
                "existing_summary": (
                    context.existing.content if context.existing is not None else None
                ),
                "messages": message_values(context.messages),
            }
        )
        return await self._runner.run(
            request=PromptRequest(
                PromptPurpose.SUMMARY,
                MemoryKind.SUMMARY,
                locale=context.locale,
                source=context.source,
                override=context.override,
            ),
            schema_version=OutputSchemaVersion.SUMMARY_V1,
            output_type=SummaryOutput,
            variables={"conversation": conversation},
        )


class FactExtractor:
    """Default fact strategy that excludes non-user-authored messages."""

    def __init__(self, runner: PromptRunner, user_message_filter: UserMessageFilter) -> None:
        self._runner = runner
        self._user_message_filter = user_message_filter

    async def extract(self, context: ExtractionContext) -> PromptCallResult[FactOutput] | None:
        """Return no model call when the configured user-message set is empty."""

        selected = self._user_message_filter.select(context.messages)
        if not selected:
            return None
        return await self._runner.run(
            request=PromptRequest(
                PromptPurpose.EXTRACTION,
                MemoryKind.FACT,
                locale=context.locale,
                source=context.source,
                override=context.override,
            ),
            schema_version=OutputSchemaVersion.FACT_V1,
            output_type=FactOutput,
            variables={"conversation": render_json(message_values(selected))},
        )


class ProfileExtractor:
    """Default profile model strategy with optional prior-profile context."""

    def __init__(self, runner: PromptRunner) -> None:
        self._runner = runner

    async def extract(self, context: ExtractionContext) -> PromptCallResult[ProfileOutput]:
        """Generate and strictly parse one optional profile candidate."""

        conversation = render_json(
            {
                "existing_profile": (
                    context.existing.content if context.existing is not None else None
                ),
                "messages": message_values(context.messages),
            }
        )
        return await self._runner.run(
            request=PromptRequest(
                PromptPurpose.PROFILE,
                MemoryKind.PROFILE,
                locale=context.locale,
                source=context.source,
                override=context.override,
            ),
            schema_version=OutputSchemaVersion.PROFILE_V1,
            output_type=ProfileOutput,
            variables={"conversation": conversation},
        )
