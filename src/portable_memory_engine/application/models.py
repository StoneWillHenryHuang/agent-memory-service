"""Immutable application commands, limits, and content-free add results."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from portable_memory_engine.domain import (
    ConversationMessage,
    DomainValidationError,
    FrozenJsonObject,
    JsonValue,
    MemoryEvent,
    MemoryKind,
    MemoryScope,
)
from portable_memory_engine.ports import PromptTemplate

_LOCALE_PATTERN = re.compile(r"[a-z]{2,8}(?:-[a-z0-9]{1,8})*\Z")


def _identifier(value: str, *, field_name: str, max_length: int = 255) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be an identifier")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > max_length
        or normalized == "*"
        or normalized != normalized.strip()
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in normalized
        )
    ):
        raise DomainValidationError(f"{field_name} must be an identifier")
    return normalized


def _optional_identifier(
    value: str | None, *, field_name: str, max_length: int = 255
) -> str | None:
    return (
        None if value is None else _identifier(value, field_name=field_name, max_length=max_length)
    )


@dataclass(frozen=True, slots=True)
class PromptOverrides:
    """Optional per-command replacements for the four built-in prompt calls."""

    summary: PromptTemplate | None = field(default=None, repr=False)
    fact: PromptTemplate | None = field(default=None, repr=False)
    profile: PromptTemplate | None = field(default=None, repr=False)
    update: PromptTemplate | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in ("summary", "fact", "profile", "update"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, PromptTemplate):
                raise DomainValidationError(f"{name} prompt override must be PromptTemplate")


def _default_kinds() -> frozenset[MemoryKind]:
    return frozenset((MemoryKind.SUMMARY, MemoryKind.FACT, MemoryKind.PROFILE))


@dataclass(frozen=True, slots=True)
class AddMemoryCommand:
    """One scoped, idempotent conversation-memory extraction command."""

    scope: MemoryScope
    messages: Sequence[ConversationMessage] = field(repr=False)
    idempotency_key: str = field(repr=False)
    event_timestamp: datetime
    kinds: frozenset[MemoryKind] = field(default_factory=_default_kinds)
    source: str | None = field(default=None, repr=False)
    session_id: str | None = field(default=None, repr=False)
    locale: str = "en"
    profile_name: str = field(default="default", repr=False)
    metadata: Mapping[str, JsonValue] = field(
        default_factory=FrozenJsonObject,
        repr=False,
    )
    prompt_overrides: PromptOverrides = field(
        default_factory=PromptOverrides,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("add scope must be MemoryScope")
        messages = tuple(self.messages)
        if not messages or any(
            not isinstance(message, ConversationMessage) for message in messages
        ):
            raise DomainValidationError(
                "add messages must contain at least one ConversationMessage"
            )
        kinds = frozenset(self.kinds)
        if not kinds or any(not isinstance(kind, MemoryKind) for kind in kinds):
            raise DomainValidationError("add kinds must contain MemoryKind values")
        if (
            not isinstance(self.event_timestamp, datetime)
            or self.event_timestamp.tzinfo is None
            or self.event_timestamp.utcoffset() is None
        ):
            raise DomainValidationError("add event_timestamp must be timezone-aware")
        if not isinstance(self.locale, str):
            raise DomainValidationError("locale must be a language tag")
        locale = unicodedata.normalize("NFC", self.locale).lower()
        if not _LOCALE_PATTERN.fullmatch(locale):
            raise DomainValidationError("locale must be a language tag such as en or en-us")
        source = _optional_identifier(self.source, field_name="source", max_length=128)
        session_id = _optional_identifier(self.session_id, field_name="session_id")
        if MemoryKind.SUMMARY in kinds and session_id is None:
            raise DomainValidationError("the default summary strategy requires session_id")
        if not isinstance(self.prompt_overrides, PromptOverrides):
            raise DomainValidationError("prompt_overrides must be PromptOverrides")
        if not isinstance(self.metadata, Mapping):
            raise DomainValidationError("add metadata must be a mapping")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "kinds", kinds)
        object.__setattr__(
            self,
            "idempotency_key",
            _identifier(self.idempotency_key, field_name="idempotency_key"),
        )
        object.__setattr__(self, "event_timestamp", self.event_timestamp.astimezone(UTC))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "locale", locale)
        object.__setattr__(
            self,
            "profile_name",
            _identifier(self.profile_name, field_name="profile_name"),
        )
        object.__setattr__(self, "metadata", FrozenJsonObject(self.metadata))


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    """Bounded application limits enforced before provider or store work."""

    max_messages: int = 100
    max_message_characters: int = 100_000
    max_prompt_characters: int = 200_000
    max_existing_memories: int = 200
    max_existing_content_characters: int = 100_000
    max_conflict_retries: int = 1

    def __post_init__(self) -> None:
        for name in (
            "max_messages",
            "max_message_characters",
            "max_prompt_characters",
            "max_existing_memories",
            "max_existing_content_characters",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DomainValidationError(f"{name} must be a positive integer")
        if self.max_existing_memories > 1_000:
            raise DomainValidationError("max_existing_memories must not exceed 1000")
        if (
            isinstance(self.max_conflict_retries, bool)
            or not isinstance(self.max_conflict_retries, int)
            or not 0 <= self.max_conflict_retries <= 10
        ):
            raise DomainValidationError("max_conflict_retries must be between 0 and 10")


class IssueCode(StrEnum):
    """Bounded content-free failure categories returned by extraction."""

    VALIDATION = "validation"
    PROVIDER = "provider"
    PROVIDER_PARSE = "provider_parse"
    STORE = "store"
    CONFLICT = "conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    STALE_EVENT = "stale_event"


@dataclass(frozen=True, slots=True)
class ExtractionIssue:
    """Sanitized application issue without provider, message, or memory text."""

    code: IssueCode
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, IssueCode):
            raise DomainValidationError("issue code must be IssueCode")
        if not isinstance(self.retryable, bool):
            raise DomainValidationError("issue retryable must be boolean")


class MutationStatus(StrEnum):
    """Application status for one proposed memory mutation."""

    APPLIED = "applied"
    REPLAYED = "replayed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MemoryMutation:
    """Content-free result of one model-proposed memory mutation."""

    kind: MemoryKind
    event: MemoryEvent
    status: MutationStatus
    memory_id: str | None = field(default=None, repr=False)
    issue: ExtractionIssue | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MemoryKind):
            raise DomainValidationError("mutation kind must be MemoryKind")
        if not isinstance(self.event, MemoryEvent):
            raise DomainValidationError("mutation event must be MemoryEvent")
        if not isinstance(self.status, MutationStatus):
            raise DomainValidationError("mutation status must be MutationStatus")
        memory_id = _optional_identifier(self.memory_id, field_name="memory_id")
        if self.event is not MemoryEvent.NOOP and memory_id is None:
            raise DomainValidationError("non-noop mutations require memory_id")
        if self.event is MemoryEvent.NOOP and memory_id is not None:
            raise DomainValidationError("noop mutations must not carry memory_id")
        if self.status is MutationStatus.FAILED and self.issue is None:
            raise DomainValidationError("failed mutations require an issue")
        if self.status is not MutationStatus.FAILED and self.issue is not None:
            raise DomainValidationError("successful mutations must not carry an issue")
        object.__setattr__(self, "memory_id", memory_id)


class KindResultStatus(StrEnum):
    """Outcome category for one requested memory kind."""

    SUCCEEDED = "succeeded"
    NO_CHANGE = "no_change"
    PARTIAL = "partial"
    STALE = "stale"
    CONFLICT = "conflict"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class KindAddResult:
    """Structured outcome for one independently processed memory kind."""

    kind: MemoryKind
    status: KindResultStatus
    mutations: Sequence[MemoryMutation] = ()
    issue: ExtractionIssue | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MemoryKind):
            raise DomainValidationError("kind result kind must be MemoryKind")
        if not isinstance(self.status, KindResultStatus):
            raise DomainValidationError("kind result status must be KindResultStatus")
        mutations = tuple(self.mutations)
        if any(
            not isinstance(mutation, MemoryMutation) or mutation.kind != self.kind
            for mutation in mutations
        ):
            raise DomainValidationError("kind result mutations must match its kind")
        failed = self.status in {
            KindResultStatus.PARTIAL,
            KindResultStatus.STALE,
            KindResultStatus.CONFLICT,
            KindResultStatus.FAILED,
        }
        if failed != (self.issue is not None):
            raise DomainValidationError("kind result failure status and issue must agree")
        object.__setattr__(self, "mutations", mutations)

    @property
    def accepted(self) -> bool:
        """Return whether this kind completed without a terminal issue."""

        return self.status in {KindResultStatus.SUCCEEDED, KindResultStatus.NO_CHANGE}

    @property
    def has_success(self) -> bool:
        """Return whether this kind contains any successful work."""

        return self.accepted or self.status is KindResultStatus.PARTIAL


class AddResultStatus(StrEnum):
    """Overall state across all requested memory kinds."""

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AddMemoryResult:
    """Content-free aggregate result for ``MemoryEngine.add``."""

    scope: MemoryScope
    status: AddResultStatus
    kind_results: Sequence[KindAddResult]
    idempotency_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("add result scope must be MemoryScope")
        if not isinstance(self.status, AddResultStatus):
            raise DomainValidationError("add result status must be AddResultStatus")
        results = tuple(self.kind_results)
        if not results or any(not isinstance(result, KindAddResult) for result in results):
            raise DomainValidationError("add result requires KindAddResult values")
        if len({result.kind for result in results}) != len(results):
            raise DomainValidationError("add result must not repeat a memory kind")
        accepted_count = sum(result.accepted for result in results)
        expected = (
            AddResultStatus.SUCCEEDED
            if accepted_count == len(results)
            else AddResultStatus.FAILED
            if not any(result.has_success for result in results)
            else AddResultStatus.PARTIAL
        )
        if self.status is not expected:
            raise DomainValidationError("add result status does not match kind results")
        object.__setattr__(self, "kind_results", results)
        object.__setattr__(
            self,
            "idempotency_key",
            _identifier(self.idempotency_key, field_name="idempotency_key"),
        )

    @classmethod
    def from_kind_results(
        cls,
        *,
        scope: MemoryScope,
        idempotency_key: str,
        kind_results: Sequence[KindAddResult],
    ) -> AddMemoryResult:
        """Derive the only valid overall state from per-kind results."""

        results = tuple(kind_results)
        accepted_count = sum(result.accepted for result in results)
        status = (
            AddResultStatus.SUCCEEDED
            if accepted_count == len(results)
            else AddResultStatus.FAILED
            if not any(result.has_success for result in results)
            else AddResultStatus.PARTIAL
        )
        return cls(scope, status, results, idempotency_key)
