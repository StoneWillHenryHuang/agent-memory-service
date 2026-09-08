"""HTTP-only Pydantic schemas for the optional reference service."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(min_length=1, max_length=255)]
Content = Annotated[str, Field(min_length=1, max_length=100_000)]


class HttpModel(BaseModel):
    """Forbid undeclared transport fields independently of domain validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ScopeSchema(HttpModel):
    tenant_id: Identifier | None = None
    subject_id: Identifier
    namespace: Identifier = "default"


class SubjectSchema(HttpModel):
    tenant_id: Identifier | None = None
    subject_id: Identifier


class MessageSchema(HttpModel):
    role: Identifier
    content: Content = Field(repr=False)
    timestamp: datetime | None = None


class AddRequest(HttpModel):
    scope: ScopeSchema
    messages: list[MessageSchema] = Field(min_length=1, max_length=100, repr=False)
    idempotency_key: Identifier = Field(repr=False)
    event_timestamp: datetime
    kinds: list[str] | None = Field(default=None, max_length=100)
    source: Identifier | None = Field(default=None, repr=False)
    session_id: Identifier | None = Field(default=None, repr=False)
    locale: Annotated[str, Field(min_length=2, max_length=64)] = "en"
    profile_name: Identifier = Field(default="default", repr=False)


class IssueResponse(HttpModel):
    code: str
    retryable: bool


class MutationResponse(HttpModel):
    kind: str
    event: str
    status: str
    memory_id: str | None = None
    issue: IssueResponse | None = None


class KindAddResponse(HttpModel):
    kind: str
    status: str
    mutations: list[MutationResponse]
    issue: IssueResponse | None = None


class AddResponse(HttpModel):
    status: str
    kind_results: list[KindAddResponse]


class RecallRequestSchema(HttpModel):
    scope: ScopeSchema
    mode: Literal["recency", "semantic"] = "recency"
    semantic_text: Content | None = Field(default=None, repr=False)
    kinds: list[str] = Field(default_factory=list, max_length=100)
    source: Identifier | None = Field(default=None, repr=False)
    session_id: Identifier | None = Field(default=None, repr=False)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryResponse(HttpModel):
    id: str
    scope: ScopeSchema
    kind: str
    content: str = Field(repr=False)
    created_at: datetime
    updated_at: datetime
    source: str | None = None
    session_id: str | None = None
    version: str | int | None = None
    score: float | None = None


class RecallResponse(HttpModel):
    mode: str
    items: list[MemoryResponse]
    offset: int
    limit: int
    next_offset: int | None
    total_count: int | None
    count_precision: str


class DeleteRequestBase(HttpModel):
    idempotency_key: Identifier = Field(repr=False)
    payload_digest: Identifier = Field(repr=False)
    event_timestamp: datetime


class DeleteMemoryRequest(DeleteRequestBase):
    target: Literal["memory_id"]
    scope: ScopeSchema
    memory_id: Identifier = Field(repr=False)


class DeleteScopeRequest(DeleteRequestBase):
    target: Literal["scope"]
    scope: ScopeSchema
    kinds: list[str] = Field(default_factory=list, max_length=100)


class DeleteSubjectRequest(DeleteRequestBase):
    target: Literal["subject"]
    subject: SubjectSchema
    kinds: list[str] = Field(default_factory=list, max_length=100)


class DeleteSessionRequest(DeleteRequestBase):
    target: Literal["session"]
    scope: ScopeSchema
    session_id: Identifier = Field(repr=False)


DeleteRequest = Annotated[
    DeleteMemoryRequest | DeleteScopeRequest | DeleteSubjectRequest | DeleteSessionRequest,
    Field(discriminator="target"),
]


class DeleteResponse(HttpModel):
    matched_count: int
    hard_deleted_count: int
    contribution_deleted_count: int
    tombstoned_count: int
    already_absent_count: int
    deleted_count: int
    barrier_timestamp: datetime
    replayed: bool


class HealthResponse(HttpModel):
    status: Literal["healthy"] = "healthy"


class ErrorBody(HttpModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(HttpModel):
    error: ErrorBody
