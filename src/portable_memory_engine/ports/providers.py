"""Chat, embedding, and prompt-provider ports with neutral request values."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    FrozenJsonObject,
    MemoryKind,
)
from portable_memory_engine.ports.lifecycle import AsyncLifecycle

_LOCALE_PATTERN = re.compile(r"[a-z]{2,8}(?:-[a-z0-9]{1,8})*\Z")


def _content(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise DomainValidationError(f"{field_name} must be non-empty text without NUL")
    return unicodedata.normalize("NFC", value)


def _provider_content(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise DomainValidationError("chat content must be text without NUL")
    return unicodedata.normalize("NFC", value)


def _identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be a non-empty identifier")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > 255
        or normalized == "*"
        or normalized != normalized.strip()
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in normalized
        )
    ):
        raise DomainValidationError(f"{field_name} must be a non-empty identifier")
    return normalized


def _locale(value: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("locale must be a language tag")
    normalized = unicodedata.normalize("NFC", value).lower()
    if not _LOCALE_PATTERN.fullmatch(normalized):
        raise DomainValidationError("locale must be a language tag such as en or en-us")
    return normalized


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Provider-neutral chat request containing immutable domain messages."""

    messages: Sequence[ConversationMessage]
    response_schema: FrozenJsonObject | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not messages or any(
            not isinstance(message, ConversationMessage) for message in messages
        ):
            raise DomainValidationError(
                "chat request messages must contain ConversationMessage values"
            )
        if self.response_schema is not None and not isinstance(
            self.response_schema, FrozenJsonObject
        ):
            raise DomainValidationError("response_schema must be FrozenJsonObject or None")
        object.__setattr__(self, "messages", messages)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Provider-neutral token accounting; zero means not reported."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("total_tokens", self.total_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DomainValidationError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Sanitized chat result without a native provider response object."""

    content: str = field(repr=False)
    model_id: str
    usage: TokenUsage = field(default_factory=TokenUsage)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", _provider_content(self.content))
        object.__setattr__(self, "model_id", _identifier(self.model_id, field_name="model_id"))
        if not isinstance(self.usage, TokenUsage):
            raise DomainValidationError("usage must be TokenUsage")

    @property
    def prompt_tokens(self) -> int:
        """Return the legacy prompt-token view of provider-neutral input usage."""

        return self.usage.input_tokens

    @property
    def completion_tokens(self) -> int:
        """Return the legacy completion-token view of provider-neutral output usage."""

        return self.usage.output_tokens


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    """One immutable batch of texts sharing a provider-neutral embedding task."""

    texts: Sequence[str] = field(repr=False)
    task: EmbeddingTask

    def __post_init__(self) -> None:
        if not isinstance(self.task, EmbeddingTask):
            raise DomainValidationError("embedding request task must be EmbeddingTask")
        texts = tuple(_content(text, field_name="embedding text") for text in self.texts)
        if not texts:
            raise DomainValidationError("embedding request texts must not be empty")
        object.__setattr__(self, "texts", texts)


class PromptPurpose(StrEnum):
    """Stable application purpose used to select a prompt definition."""

    SUMMARY = "summary"
    EXTRACTION = "extraction"
    UPDATE = "update"
    PROFILE = "profile"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """Versioned prompt text whose rendering belongs to the application layer."""

    identifier: str
    version: str
    system_text: str = field(repr=False)
    user_text: str = field(repr=False)
    response_schema_version: str | None = None
    response_schema: FrozenJsonObject | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "identifier", _identifier(self.identifier, field_name="prompt identifier")
        )
        object.__setattr__(self, "version", _identifier(self.version, field_name="prompt version"))
        object.__setattr__(
            self,
            "system_text",
            _content(self.system_text, field_name="prompt system_text"),
        )
        object.__setattr__(
            self,
            "user_text",
            _content(self.user_text, field_name="prompt user_text"),
        )
        if self.response_schema_version is not None:
            object.__setattr__(
                self,
                "response_schema_version",
                _identifier(
                    self.response_schema_version,
                    field_name="response_schema_version",
                ),
            )
        if self.response_schema is not None and not isinstance(
            self.response_schema, FrozenJsonObject
        ):
            raise DomainValidationError("response_schema must be FrozenJsonObject or None")
        if (self.response_schema_version is None) != (self.response_schema is None):
            raise DomainValidationError(
                "response_schema_version and response_schema must be provided together"
            )


@dataclass(frozen=True, slots=True)
class PromptRequest:
    """Neutral prompt lookup key with locale and an optional caller override."""

    purpose: PromptPurpose
    kind: MemoryKind
    locale: str = "en"
    source: str | None = field(default=None, repr=False)
    override: PromptTemplate | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, PromptPurpose):
            raise DomainValidationError("prompt purpose must be PromptPurpose")
        if not isinstance(self.kind, MemoryKind):
            raise DomainValidationError("prompt kind must be MemoryKind")
        object.__setattr__(self, "locale", _locale(self.locale))
        if self.source is not None:
            object.__setattr__(self, "source", _identifier(self.source, field_name="source"))
        if self.override is not None and not isinstance(self.override, PromptTemplate):
            raise DomainValidationError("prompt override must be PromptTemplate or None")


@runtime_checkable
class ChatModel(AsyncLifecycle, Protocol):
    """Asynchronous chat boundary returning only sanitized public values."""

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Generate one response or raise a sanitized provider exception."""

        ...


@runtime_checkable
class Embedder(AsyncLifecycle, Protocol):
    """Asynchronous batch embedding boundary with explicit task identity."""

    async def embed(self, request: EmbeddingRequest) -> tuple[Embedding, ...]:
        """Return one embedding per input text in the original order."""

        ...


@runtime_checkable
class PromptProvider(AsyncLifecycle, Protocol):
    """Asynchronous source of versioned, provider-independent prompts."""

    async def get_prompt(self, request: PromptRequest) -> PromptTemplate:
        """Resolve one prompt definition without rendering content-bearing messages."""

        ...
