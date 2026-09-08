"""Explicit security configuration for the optional FastAPI reference app."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from portable_memory_engine import DomainValidationError


def _host(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value == "*"
        or value.startswith("*.")
        or value != value.strip()
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        raise DomainValidationError("allowed_hosts must contain explicit host names")
    return value


def _origin(value: str) -> str:
    if not isinstance(value, str) or "*" in value or value != value.strip():
        raise DomainValidationError("cors_origins must contain explicit HTTP origins")
    parsed = urlsplit(value)
    try:
        _ = parsed.port
    except ValueError:
        raise DomainValidationError("cors_origins must contain explicit HTTP origins") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DomainValidationError("cors_origins must contain explicit HTTP origins")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class ReferenceServiceConfig:
    """Caller-owned localhost, CORS, and engine-lifecycle settings."""

    allowed_hosts: tuple[str, ...] = field(
        default=("localhost", "127.0.0.1", "[::1]"),
        repr=False,
    )
    cors_origins: tuple[str, ...] = field(default=(), repr=False)
    owns_engine: bool = True

    def __post_init__(self) -> None:
        hosts = tuple(_host(value) for value in self.allowed_hosts)
        origins = tuple(_origin(value) for value in self.cors_origins)
        if not hosts:
            raise DomainValidationError("allowed_hosts must not be empty")
        if len(hosts) != len(set(hosts)) or len(origins) != len(set(origins)):
            raise DomainValidationError("host and origin settings must not contain duplicates")
        if not isinstance(self.owns_engine, bool):
            raise DomainValidationError("owns_engine must be boolean")
        object.__setattr__(self, "allowed_hosts", hosts)
        object.__setattr__(self, "cors_origins", origins)
