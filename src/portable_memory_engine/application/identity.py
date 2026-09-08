"""Versioned deterministic identity strategies for built-in memory kinds."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Protocol, runtime_checkable

from portable_memory_engine.domain import DomainValidationError, MemoryScope


@runtime_checkable
class IdentityStrategy(Protocol):
    """Replaceable identity strategy for summary, fact, and profile records."""

    def summary_id(self, *, scope: MemoryScope, session_id: str) -> str: ...

    def fact_id(self, *, scope: MemoryScope, content: str) -> str: ...

    def profile_id(self, *, scope: MemoryScope, profile_name: str) -> str: ...

    def fact_canonical(self, content: str) -> str: ...

    def fact_guard_id(self, *, scope: MemoryScope) -> str: ...


class DefaultIdentityStrategy:
    """SHA-256 identity scheme with explicit, stable v1 canonicalization."""

    _VERSION = "identity.v1"

    @staticmethod
    def _scope(scope: MemoryScope) -> list[str | None]:
        if not isinstance(scope, MemoryScope):
            raise DomainValidationError("identity scope must be MemoryScope")
        return [scope.tenant_id, scope.subject_id, scope.namespace]

    @classmethod
    def _identifier(cls, prefix: str, components: list[str | None]) -> str:
        payload = json.dumps(
            [cls._VERSION, prefix, *components],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return f"{prefix}-v1-{hashlib.sha256(payload).hexdigest()}"

    @staticmethod
    def _text(value: str, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise DomainValidationError(f"{field_name} must be text")
        normalized = unicodedata.normalize("NFC", value)
        if not normalized.strip() or "\x00" in normalized:
            raise DomainValidationError(f"{field_name} must be non-empty text without NUL")
        return normalized

    def summary_id(self, *, scope: MemoryScope, session_id: str) -> str:
        """Return one stable summary ID per complete scope and session."""

        session = self._text(session_id, field_name="session_id")
        return self._identifier("summary", [*self._scope(scope), session])

    def fact_canonical(self, content: str) -> str:
        """Apply the deliberately narrow v1 fact identity normalization."""

        normalized = self._text(content, field_name="fact content")
        return " ".join(normalized.split()).casefold()

    def fact_id(self, *, scope: MemoryScope, content: str) -> str:
        """Return one stable ID for canonical fact content within a scope."""

        return self._identifier(
            "fact",
            [*self._scope(scope), self.fact_canonical(content)],
        )

    def profile_id(self, *, scope: MemoryScope, profile_name: str) -> str:
        """Return one stable profile ID per complete scope and profile name."""

        name = self._text(profile_name, field_name="profile_name")
        return self._identifier("profile", [*self._scope(scope), name])

    def fact_guard_id(self, *, scope: MemoryScope) -> str:
        """Return a non-record ID used only to observe scope deletion barriers."""

        return self._identifier("fact-guard", self._scope(scope))
