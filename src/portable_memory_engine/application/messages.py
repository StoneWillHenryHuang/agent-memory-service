"""Bounded message selection and provider-neutral JSON rendering."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from portable_memory_engine.application.models import ExtractionLimits
from portable_memory_engine.domain import ConversationMessage, DomainValidationError


@runtime_checkable
class UserMessageFilter(Protocol):
    """Strategy selecting messages authored by the memory subject."""

    def select(
        self, messages: Sequence[ConversationMessage]
    ) -> tuple[ConversationMessage, ...]: ...


@dataclass(frozen=True, slots=True)
class RoleBasedUserMessageFilter:
    """Select messages whose case-insensitive role is explicitly configured."""

    roles: frozenset[str] = frozenset({"user"})

    def __post_init__(self) -> None:
        roles = frozenset(
            unicodedata.normalize("NFC", role).casefold()
            for role in self.roles
            if isinstance(role, str) and role
        )
        if not roles or len(roles) != len(self.roles):
            raise DomainValidationError("user message roles must be non-empty text values")
        object.__setattr__(self, "roles", roles)

    def select(self, messages: Sequence[ConversationMessage]) -> tuple[ConversationMessage, ...]:
        """Return user-authored messages in their original order."""

        if any(not isinstance(message, ConversationMessage) for message in messages):
            raise DomainValidationError("message filter requires ConversationMessage values")
        return tuple(message for message in messages if message.role.casefold() in self.roles)


def validate_message_limits(
    messages: Sequence[ConversationMessage], limits: ExtractionLimits
) -> None:
    """Reject oversized input before access to a model provider."""

    if len(messages) > limits.max_messages:
        raise DomainValidationError("message count exceeds the extraction limit")
    if sum(len(message.content) for message in messages) > limits.max_message_characters:
        raise DomainValidationError("message content exceeds the extraction limit")


def message_values(messages: Sequence[ConversationMessage]) -> list[dict[str, str | None]]:
    """Return a minimal synthetic-wire-like value without message metadata."""

    return [
        {
            "role": message.role,
            "content": message.content,
            "timestamp": message.timestamp.isoformat() if message.timestamp is not None else None,
        }
        for message in messages
    ]


def render_json(value: object) -> str:
    """Render deterministic compact JSON for an untrusted prompt placeholder."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
