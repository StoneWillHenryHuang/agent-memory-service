"""Explicit configuration for the optional Google Vertex embedding adapter."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from portable_memory_engine.domain import DomainValidationError


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


@dataclass(frozen=True, slots=True)
class GoogleVertexEmbeddingConfig:
    """Caller-owned Vertex location, model, dimension, and batching settings."""

    project: str = field(repr=False)
    region: str = field(repr=False)
    model: str = field(repr=False)
    dimension: int
    batch_size: int = 1
    auto_truncate: bool = False
    api_version: str = field(default="v1", repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "project", _identifier(self.project, field_name="project"))
        object.__setattr__(self, "region", _identifier(self.region, field_name="region"))
        object.__setattr__(self, "model", _identifier(self.model, field_name="model"))
        object.__setattr__(
            self,
            "api_version",
            _identifier(self.api_version, field_name="api_version"),
        )
        if (
            isinstance(self.dimension, bool)
            or not isinstance(self.dimension, int)
            or not 1 <= self.dimension <= 16_000
        ):
            raise DomainValidationError("dimension must be between 1 and 16000")
        if (
            isinstance(self.batch_size, bool)
            or not isinstance(self.batch_size, int)
            or not 1 <= self.batch_size <= 250
        ):
            raise DomainValidationError("batch_size must be between 1 and 250")
        if not isinstance(self.auto_truncate, bool):
            raise DomainValidationError("auto_truncate must be a boolean")
