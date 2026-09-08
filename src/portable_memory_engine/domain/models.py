"""Immutable domain models and mutation result values.

These Python values are not wire schemas. Integrations and adapters must map them
explicitly to versioned JSON, HTTP, ORM, or provider representations.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from portable_memory_engine.domain._validation import (
    normalize_content,
    normalize_identifier,
    normalize_kinds,
    normalize_metadata,
    normalize_optional_identifier,
    normalize_optional_utc,
    normalize_utc,
    normalize_version,
)
from portable_memory_engine.domain.enums import (
    DeleteTarget,
    EmbeddingTask,
    MemoryKind,
    PutStatus,
)
from portable_memory_engine.domain.errors import DomainValidationError
from portable_memory_engine.domain.types import FrozenJsonObject, JsonValue, VersionToken


@dataclass(frozen=True, slots=True, repr=False)
class MemorySubject:
    """One tenant-and-subject boundary spanning that subject's namespaces."""

    subject_id: str
    tenant_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_id",
            normalize_identifier(self.subject_id, field_name="subject_id"),
        )
        object.__setattr__(
            self,
            "tenant_id",
            normalize_optional_identifier(self.tenant_id, field_name="tenant_id"),
        )

    def __repr__(self) -> str:
        """Return a content-safe representation for diagnostics."""

        tenant = "set" if self.tenant_id is not None else "none"
        return f"MemorySubject(tenant_id=<{tenant}>, subject_id=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class MemoryScope:
    """The optional-tenant, mandatory-subject-and-namespace isolation boundary."""

    subject_id: str
    namespace: str = "default"
    tenant_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_id",
            normalize_identifier(self.subject_id, field_name="subject_id"),
        )
        object.__setattr__(
            self,
            "namespace",
            normalize_identifier(self.namespace, field_name="namespace"),
        )
        object.__setattr__(
            self,
            "tenant_id",
            normalize_optional_identifier(self.tenant_id, field_name="tenant_id"),
        )

    def __repr__(self) -> str:
        """Return a content-safe representation for diagnostics."""

        tenant = "set" if self.tenant_id is not None else "none"
        return f"MemoryScope(tenant_id=<{tenant}>, subject_id=<redacted>, namespace=<redacted>)"

    @property
    def subject(self) -> MemorySubject:
        """Return the explicit cross-namespace boundary for this subject."""

        return MemorySubject(subject_id=self.subject_id, tenant_id=self.tenant_id)


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    """Minimal origin and schema information retained with a memory."""

    source: str | None = field(default=None, repr=False)
    session_id: str | None = field(default=None, repr=False)
    event_timestamp: datetime | None = None
    input_digest: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            normalize_optional_identifier(self.source, field_name="source", max_length=128),
        )
        object.__setattr__(
            self,
            "session_id",
            normalize_optional_identifier(self.session_id, field_name="session_id"),
        )
        object.__setattr__(
            self,
            "event_timestamp",
            normalize_optional_utc(self.event_timestamp, field_name="event_timestamp"),
        )
        for field_name in ("input_digest", "model_id", "prompt_version", "schema_version"):
            object.__setattr__(
                self,
                field_name,
                normalize_optional_identifier(getattr(self, field_name), field_name=field_name),
            )


@dataclass(frozen=True, slots=True)
class Embedding:
    """An immutable provider-neutral embedding with model and task identity."""

    values: Sequence[float] = field(repr=False)
    model_id: str
    task: EmbeddingTask

    def __post_init__(self) -> None:
        if not isinstance(self.task, EmbeddingTask):
            raise DomainValidationError("embedding task must be an EmbeddingTask")
        model_id = normalize_identifier(self.model_id, field_name="embedding model_id")
        normalized_values: list[float] = []
        for value in self.values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DomainValidationError("embedding values must be numeric")
            number = float(value)
            if not math.isfinite(number):
                raise DomainValidationError("embedding values must be finite")
            normalized_values.append(number)
        if not normalized_values:
            raise DomainValidationError("embedding values must not be empty")
        object.__setattr__(self, "values", tuple(normalized_values))
        object.__setattr__(self, "model_id", model_id)

    @property
    def dimension(self) -> int:
        """Return the immutable vector dimension."""

        return len(self.values)


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One normalized conversation message supplied to extraction."""

    role: str
    content: str = field(repr=False)
    timestamp: datetime | None = None
    metadata: Mapping[str, JsonValue] = field(
        default_factory=FrozenJsonObject,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", normalize_identifier(self.role, field_name="role"))
        object.__setattr__(
            self,
            "content",
            normalize_content(self.content, field_name="message content"),
        )
        object.__setattr__(
            self,
            "timestamp",
            normalize_optional_utc(self.timestamp, field_name="message timestamp"),
        )
        object.__setattr__(self, "metadata", normalize_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One immutable memory value identified only within its complete scope."""

    id: str
    scope: MemoryScope
    kind: MemoryKind
    content: str = field(repr=False)
    created_at: datetime
    updated_at: datetime
    provenance: MemoryProvenance = field(default_factory=MemoryProvenance)
    metadata: Mapping[str, JsonValue] = field(
        default_factory=FrozenJsonObject,
        repr=False,
    )
    embedding: Embedding | None = field(default=None, repr=False)
    version: VersionToken | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        if not isinstance(self.kind, MemoryKind):
            raise DomainValidationError("kind must be a MemoryKind")
        if not isinstance(self.provenance, MemoryProvenance):
            raise DomainValidationError("provenance must be MemoryProvenance")
        if self.embedding is not None and not isinstance(self.embedding, Embedding):
            raise DomainValidationError("embedding must be an Embedding or None")

        created_at = normalize_utc(self.created_at, field_name="created_at")
        updated_at = normalize_utc(self.updated_at, field_name="updated_at")
        if updated_at < created_at:
            raise DomainValidationError("updated_at must not be earlier than created_at")

        object.__setattr__(self, "id", normalize_identifier(self.id, field_name="memory id"))
        object.__setattr__(
            self,
            "content",
            normalize_content(self.content, field_name="memory content"),
        )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "metadata", normalize_metadata(self.metadata))
        object.__setattr__(self, "version", normalize_version(self.version, field_name="version"))


@dataclass(frozen=True, slots=True)
class ConditionalWrite:
    """A scoped record mutation guarded by version and command identity."""

    record: MemoryRecord
    idempotency_key: str = field(repr=False)
    payload_digest: str = field(repr=False)
    event_timestamp: datetime
    expected_version: VersionToken | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.record, MemoryRecord):
            raise DomainValidationError("record must be a MemoryRecord")
        object.__setattr__(
            self,
            "idempotency_key",
            normalize_identifier(self.idempotency_key, field_name="idempotency_key"),
        )
        object.__setattr__(
            self,
            "payload_digest",
            normalize_identifier(self.payload_digest, field_name="payload_digest"),
        )
        object.__setattr__(
            self,
            "event_timestamp",
            normalize_utc(self.event_timestamp, field_name="write event_timestamp"),
        )
        object.__setattr__(
            self,
            "expected_version",
            normalize_version(self.expected_version, field_name="expected_version"),
        )


@dataclass(frozen=True, slots=True)
class PutOutcome:
    """Sanitized persistence outcome for one scoped memory identity."""

    memory_id: str
    scope: MemoryScope
    status: PutStatus
    version: VersionToken
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        if not isinstance(self.status, PutStatus):
            raise DomainValidationError("status must be a PutStatus")
        if not isinstance(self.replayed, bool):
            raise DomainValidationError("replayed must be boolean")
        version = normalize_version(self.version, field_name="version")
        if version is None:
            raise DomainValidationError("put outcome version must not be None")
        object.__setattr__(
            self, "memory_id", normalize_identifier(self.memory_id, field_name="memory id")
        )
        object.__setattr__(self, "version", version)


@dataclass(frozen=True, slots=True)
class PutResult:
    """Immutable collection of successful per-record persistence outcomes."""

    outcomes: Sequence[PutOutcome] = ()

    def __post_init__(self) -> None:
        outcomes = tuple(self.outcomes)
        if any(not isinstance(outcome, PutOutcome) for outcome in outcomes):
            raise DomainValidationError("put outcomes must contain PutOutcome values")
        identities = {(outcome.scope, outcome.memory_id) for outcome in outcomes}
        if len(identities) != len(outcomes):
            raise DomainValidationError("put outcomes must not repeat a scoped memory identity")
        object.__setattr__(self, "outcomes", outcomes)

    @property
    def created_count(self) -> int:
        """Return the number of created records."""

        return sum(outcome.status is PutStatus.CREATED for outcome in self.outcomes)

    @property
    def updated_count(self) -> int:
        """Return the number of updated records."""

        return sum(outcome.status is PutStatus.UPDATED for outcome in self.outcomes)

    @property
    def unchanged_count(self) -> int:
        """Return the number of unchanged records."""

        return sum(outcome.status is PutStatus.UNCHANGED for outcome in self.outcomes)


@dataclass(frozen=True, slots=True, kw_only=True)
class _DeleteCommandBase:
    """Shared idempotency and event-time material for deletion commands."""

    idempotency_key: str = field(repr=False)
    payload_digest: str = field(repr=False)
    event_timestamp: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "idempotency_key",
            normalize_identifier(self.idempotency_key, field_name="idempotency_key"),
        )
        object.__setattr__(
            self,
            "payload_digest",
            normalize_identifier(self.payload_digest, field_name="payload_digest"),
        )
        object.__setattr__(
            self,
            "event_timestamp",
            normalize_utc(self.event_timestamp, field_name="deletion event_timestamp"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteMemoryCommand(_DeleteCommandBase):
    """Hard-delete one memory only inside its complete scope."""

    scope: MemoryScope
    memory_id: str = field(repr=False)

    def __post_init__(self) -> None:
        super(DeleteMemoryCommand, self).__post_init__()
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        object.__setattr__(
            self,
            "memory_id",
            normalize_identifier(self.memory_id, field_name="memory id"),
        )

    @property
    def target(self) -> DeleteTarget:
        """Return the command's stable target category."""

        return DeleteTarget.MEMORY_ID


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteScopeCommand(_DeleteCommandBase):
    """Hard-delete a complete scope, optionally restricted by memory kind."""

    scope: MemoryScope
    kinds: frozenset[MemoryKind] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        super(DeleteScopeCommand, self).__post_init__()
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        object.__setattr__(self, "kinds", normalize_kinds(self.kinds))

    @property
    def target(self) -> DeleteTarget:
        """Return the command's stable target category."""

        return DeleteTarget.SCOPE


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteSubjectCommand(_DeleteCommandBase):
    """Hard-delete one subject across namespaces without crossing tenants."""

    subject: MemorySubject
    kinds: frozenset[MemoryKind] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        super(DeleteSubjectCommand, self).__post_init__()
        if not isinstance(self.subject, MemorySubject):
            raise DomainValidationError("subject must be a MemorySubject")
        object.__setattr__(self, "kinds", normalize_kinds(self.kinds))

    @property
    def target(self) -> DeleteTarget:
        """Return the command's stable target category."""

        return DeleteTarget.SUBJECT


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteSessionCommand(_DeleteCommandBase):
    """Delete one session's contributions inside one complete scope."""

    scope: MemoryScope
    session_id: str = field(repr=False)

    def __post_init__(self) -> None:
        super(DeleteSessionCommand, self).__post_init__()
        if not isinstance(self.scope, MemoryScope):
            raise DomainValidationError("scope must be a MemoryScope")
        object.__setattr__(
            self,
            "session_id",
            normalize_identifier(self.session_id, field_name="session_id"),
        )

    @property
    def target(self) -> DeleteTarget:
        """Return the command's stable target category."""

        return DeleteTarget.SESSION


type DeleteCommand = (
    DeleteMemoryCommand | DeleteScopeCommand | DeleteSubjectCommand | DeleteSessionCommand
)


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """Structured, content-free result of one idempotent deletion command."""

    matched_count: int
    hard_deleted_count: int
    contribution_deleted_count: int
    tombstoned_count: int
    already_absent_count: int
    barrier_timestamp: datetime
    replayed: bool = False

    def __post_init__(self) -> None:
        counts = {
            "matched_count": self.matched_count,
            "hard_deleted_count": self.hard_deleted_count,
            "contribution_deleted_count": self.contribution_deleted_count,
            "tombstoned_count": self.tombstoned_count,
            "already_absent_count": self.already_absent_count,
        }
        for field_name, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DomainValidationError(f"{field_name} must be a non-negative integer")
        if self.deleted_count > self.matched_count:
            raise DomainValidationError("deleted counts must not exceed matched_count")
        if self.tombstoned_count > self.contribution_deleted_count:
            raise DomainValidationError(
                "tombstoned_count must not exceed contribution_deleted_count"
            )
        if not isinstance(self.replayed, bool):
            raise DomainValidationError("replayed must be boolean")
        object.__setattr__(
            self,
            "barrier_timestamp",
            normalize_utc(self.barrier_timestamp, field_name="barrier_timestamp"),
        )

    @property
    def deleted_count(self) -> int:
        """Return hard-deleted records plus deleted contributions."""

        return self.hard_deleted_count + self.contribution_deleted_count
