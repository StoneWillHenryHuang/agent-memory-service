"""Explicit, secret-safe configuration for the OpenAI-compatible adapter."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

from portable_memory_engine.domain import DomainValidationError


class StructuredOutputMode(StrEnum):
    """Select how a response schema is represented to the endpoint."""

    AUTO = "auto"
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"


class StructuredOutputCapability(StrEnum):
    """Observed or configured response-format support for this adapter."""

    UNKNOWN = "unknown"
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    UNSUPPORTED = "unsupported"


def _identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be a non-empty identifier")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > 255
        or normalized != normalized.strip()
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in normalized
        )
    ):
        raise DomainValidationError(f"{field_name} must be a non-empty identifier")
    return normalized


def _base_url(value: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("base_url must be an absolute HTTP(S) URL")
    normalized = value.rstrip("/")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
    except ValueError:
        raise DomainValidationError("base_url must be an absolute HTTP(S) URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(unicodedata.category(character).startswith("C") for character in normalized)
    ):
        raise DomainValidationError(
            "base_url must be an absolute HTTP(S) URL without credentials, query, or fragment"
        )
    return normalized


def _api_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise DomainValidationError("api_key must be non-empty text without control characters")
    return value


def _positive_number(value: float, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainValidationError(f"{field_name} must be a positive finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise DomainValidationError(f"{field_name} must be a positive finite number")
    return normalized


@dataclass(frozen=True, slots=True)
class OpenAICompatibleChatConfig:
    """Caller-owned settings with no environment or gateway defaults."""

    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 120.0
    max_retries: int = 3
    retry_backoff_seconds: float = 0.5
    max_retry_delay_seconds: float = 30.0
    max_response_bytes: int = 1_000_000
    structured_output_mode: StructuredOutputMode = StructuredOutputMode.AUTO

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _base_url(self.base_url))
        object.__setattr__(self, "api_key", _api_key(self.api_key))
        object.__setattr__(self, "model", _identifier(self.model, field_name="model"))
        object.__setattr__(
            self,
            "timeout_seconds",
            _positive_number(self.timeout_seconds, field_name="timeout_seconds"),
        )
        object.__setattr__(
            self,
            "retry_backoff_seconds",
            _positive_number(
                self.retry_backoff_seconds,
                field_name="retry_backoff_seconds",
            ),
        )
        object.__setattr__(
            self,
            "max_retry_delay_seconds",
            _positive_number(
                self.max_retry_delay_seconds,
                field_name="max_retry_delay_seconds",
            ),
        )
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= 10
        ):
            raise DomainValidationError("max_retries must be an integer between 0 and 10")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes < 1
        ):
            raise DomainValidationError("max_response_bytes must be a positive integer")
        if not isinstance(self.structured_output_mode, StructuredOutputMode):
            raise DomainValidationError("structured_output_mode must be StructuredOutputMode")
