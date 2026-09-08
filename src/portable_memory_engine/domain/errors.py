"""Framework-independent exceptions exposed by the domain boundary."""

from __future__ import annotations

from datetime import datetime


class MemoryEngineError(Exception):
    """Base class for expected Portable Memory Engine failures."""


class DomainValidationError(MemoryEngineError):
    """Raised when a domain value violates a public invariant."""


class CapabilityError(MemoryEngineError):
    """Raised before an operation whose required adapter capability is absent."""

    def __init__(self, *, operation: str, capability: str) -> None:
        self.operation = operation
        self.capability = capability
        super().__init__(f"{operation} requires the {capability} capability")


class ConflictError(MemoryEngineError):
    """Raised when optimistic concurrency prevents a mutation."""

    def __init__(self, *, memory_id: str | None = None) -> None:
        self.memory_id = memory_id
        super().__init__("conditional write conflict")


class IdempotencyConflictError(ConflictError):
    """Raised when one scoped idempotency key is reused for another payload."""

    def __init__(self, *, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__()
        self.args = ("idempotency key was reused with a different payload",)


class StaleEventError(ConflictError):
    """Raised when an event is older than an accepted freshness watermark."""

    def __init__(self, *, event_timestamp: datetime, watermark: datetime) -> None:
        self.event_timestamp = event_timestamp
        self.watermark = watermark
        super().__init__()
        self.args = ("event is older than the accepted freshness watermark",)


class ProviderError(MemoryEngineError):
    """Base class for sanitized model or embedding provider failures."""


class ProviderUnavailableError(ProviderError):
    """Raised when a provider cannot currently complete an operation."""


class ProviderParseError(ProviderError):
    """Raised when untrusted provider output does not match its public schema."""

    def __init__(self, *, schema_version: str, reason_code: str = "schema_mismatch") -> None:
        self.schema_version = schema_version
        self.reason_code = reason_code
        super().__init__(f"provider output did not match schema {schema_version}: {reason_code}")


class PromptResolutionError(ProviderError):
    """Raised when no prompt exists for a sanitized public lookup key."""

    def __init__(self, *, locale: str, purpose: str, kind: str) -> None:
        self.locale = locale
        self.purpose = purpose
        self.kind = kind
        super().__init__(f"no prompt for locale={locale}, purpose={purpose}, kind={kind}")


class StoreError(MemoryEngineError):
    """Base class for sanitized memory-store failures."""


class StoreUnavailableError(StoreError):
    """Raised when a memory store cannot currently serve an operation."""


class AccessDeniedError(MemoryEngineError):
    """Raised when an access policy denies a scoped operation."""

    def __init__(self, *, operation: str) -> None:
        self.operation = operation
        super().__init__(f"access policy denied the {operation} operation")


class LifecycleError(MemoryEngineError):
    """Raised when an adapter is used outside its declared lifecycle."""
