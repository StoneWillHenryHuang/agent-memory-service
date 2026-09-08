"""Lifecycle configuration for the optional MCP reference server."""

from __future__ import annotations

from dataclasses import dataclass

from portable_memory_engine import DomainValidationError


@dataclass(frozen=True, slots=True)
class ReferenceMcpConfig:
    """Caller-owned engine lifecycle setting for one stdio connection."""

    owns_engine: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.owns_engine, bool):
            raise DomainValidationError("owns_engine must be boolean")
