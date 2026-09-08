"""MCP-only Pydantic schemas with no client or source conventions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Identifier = Annotated[str, Field(min_length=1, max_length=255)]
Content = Annotated[str, Field(min_length=1, max_length=100_000)]


class McpModel(BaseModel):
    """Forbid undeclared tool fields independently of domain validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ScopeInput(McpModel):
    tenant_id: Identifier | None = None
    subject_id: Identifier
    namespace: Identifier = "default"


class SubjectInput(McpModel):
    tenant_id: Identifier | None = None
    subject_id: Identifier


class MessageInput(McpModel):
    role: Identifier
    content: Content = Field(repr=False)
    timestamp: datetime | None = None


class AddToolInput(McpModel):
    scope: ScopeInput
    messages: list[MessageInput] = Field(min_length=1, max_length=100, repr=False)
    idempotency_key: Identifier = Field(repr=False)
    event_timestamp: datetime
    kinds: list[str] | None = Field(default=None, max_length=100)
    session_id: Identifier | None = Field(default=None, repr=False)
    locale: Annotated[str, Field(min_length=2, max_length=64)] = "en"
    profile_name: Identifier = Field(default="default", repr=False)


class RecallToolInput(McpModel):
    scope: ScopeInput
    mode: Literal["recency", "semantic"] = "recency"
    semantic_text: Content | None = Field(default=None, repr=False)
    kinds: list[str] = Field(default_factory=list, max_length=100)
    session_id: Identifier | None = Field(default=None, repr=False)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)


class DeleteInputBase(McpModel):
    idempotency_key: Identifier = Field(repr=False)
    payload_digest: Identifier = Field(repr=False)
    event_timestamp: datetime


class DeleteMemoryInput(DeleteInputBase):
    target: Literal["memory_id"]
    scope: ScopeInput
    memory_id: Identifier = Field(repr=False)


class DeleteScopeInput(DeleteInputBase):
    target: Literal["scope"]
    scope: ScopeInput
    kinds: list[str] = Field(default_factory=list, max_length=100)


class DeleteSubjectInput(DeleteInputBase):
    target: Literal["subject"]
    subject: SubjectInput
    kinds: list[str] = Field(default_factory=list, max_length=100)


class DeleteSessionInput(DeleteInputBase):
    target: Literal["session"]
    scope: ScopeInput
    session_id: Identifier = Field(repr=False)


type DeleteToolInput = Annotated[
    DeleteMemoryInput | DeleteScopeInput | DeleteSubjectInput | DeleteSessionInput,
    Field(discriminator="target"),
]
DELETE_TOOL_INPUT: TypeAdapter[DeleteToolInput] = TypeAdapter(DeleteToolInput)


def _delete_tool_input_schema() -> dict[str, Any]:
    schema = DELETE_TOOL_INPUT.json_schema()
    schema["type"] = "object"
    return schema


DELETE_TOOL_INPUT_SCHEMA: dict[str, Any] = _delete_tool_input_schema()


class IssueOutput(McpModel):
    code: str
    retryable: bool


class MutationOutput(McpModel):
    kind: str
    event: str
    status: str
    memory_id: str | None = None
    issue: IssueOutput | None = None


class KindAddOutput(McpModel):
    kind: str
    status: str
    mutations: list[MutationOutput]
    issue: IssueOutput | None = None


class AddToolOutput(McpModel):
    status: str
    kind_results: list[KindAddOutput]


class MemoryOutput(McpModel):
    id: str
    scope: ScopeInput
    kind: str
    content: str = Field(repr=False)
    created_at: datetime
    updated_at: datetime
    session_id: str | None = None
    version: str | int | None = None
    score: float | None = None


class RecallToolOutput(McpModel):
    mode: str
    items: list[MemoryOutput]
    offset: int
    limit: int
    next_offset: int | None
    total_count: int | None
    count_precision: str


class DeleteToolOutput(McpModel):
    matched_count: int
    hard_deleted_count: int
    contribution_deleted_count: int
    tombstoned_count: int
    already_absent_count: int
    deleted_count: int
    barrier_timestamp: datetime
    replayed: bool
