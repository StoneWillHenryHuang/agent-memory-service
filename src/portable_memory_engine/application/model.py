"""One strict prompt-resolution, rendering, model-call, and parse boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from portable_memory_engine.application.models import ExtractionLimits
from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    ProviderError,
    ProviderParseError,
)
from portable_memory_engine.ports import (
    ChatModel,
    ChatRequest,
    PromptProvider,
    PromptRequest,
)
from portable_memory_engine.prompts import (
    OutputSchemaVersion,
    ParsedPromptOutput,
    parse_prompt_output,
)


@dataclass(frozen=True, slots=True)
class PromptCallResult[OutputT]:
    """Parsed output plus bounded model and prompt provenance."""

    output: OutputT = field(repr=False)
    model_id: str
    prompt_version: str
    schema_version: str


class PromptRunner:
    """Resolve and execute one versioned prompt without logging its content."""

    def __init__(
        self,
        *,
        chat_model: ChatModel,
        prompt_provider: PromptProvider,
        limits: ExtractionLimits,
    ) -> None:
        self._chat_model = chat_model
        self._prompt_provider = prompt_provider
        self._limits = limits

    async def run[OutputT: ParsedPromptOutput](
        self,
        *,
        request: PromptRequest,
        schema_version: OutputSchemaVersion,
        output_type: type[OutputT],
        variables: Mapping[str, str],
    ) -> PromptCallResult[OutputT]:
        """Return one strictly parsed result or a sanitized provider failure."""

        template = await self._prompt_provider.get_prompt(request)
        if (
            template.response_schema_version != schema_version.value
            or template.response_schema is None
        ):
            raise ProviderParseError(
                schema_version=schema_version.value,
                reason_code="prompt_schema_mismatch",
            )
        try:
            user_text = template.user_text.format_map(dict(variables))
        except (KeyError, IndexError, ValueError):
            raise ProviderError("prompt template could not be rendered") from None
        if len(template.system_text) + len(user_text) > self._limits.max_prompt_characters:
            raise DomainValidationError("rendered prompt exceeds the extraction limit")
        response = await self._chat_model.complete(
            ChatRequest(
                (
                    ConversationMessage(role="system", content=template.system_text),
                    ConversationMessage(role="user", content=user_text),
                ),
                response_schema=template.response_schema,
            )
        )
        parsed = parse_prompt_output(response.content, schema_version=schema_version)
        if not isinstance(parsed, output_type):
            raise ProviderParseError(
                schema_version=schema_version.value,
                reason_code="schema_mismatch",
            )
        return PromptCallResult(
            output=cast("OutputT", parsed),
            model_id=response.model_id,
            prompt_version=template.version,
            schema_version=schema_version.value,
        )
