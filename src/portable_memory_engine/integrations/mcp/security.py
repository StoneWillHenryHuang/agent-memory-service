"""Small injectable authorization boundary for MCP reference tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from portable_memory_engine import MemoryScope, MemorySubject


@dataclass(frozen=True, slots=True)
class McpAuthorizationContext:
    """Minimal per-call metadata supplied to an authorization hook."""

    tool_name: str
    request_id: str
    context_id: str
    boundary: MemoryScope | MemorySubject = field(repr=False)


@runtime_checkable
class McpAuthorizationHook(Protocol):
    """Reference-only hook; deployments must supply their own authorization."""

    async def authorize(self, context: McpAuthorizationContext) -> bool:
        """Return whether one tool may operate on the explicit memory boundary."""

        ...


class AllowLocalStdioAuthorization:
    """Non-production default for a caller-controlled local stdio process."""

    async def authorize(self, context: McpAuthorizationContext) -> bool:
        """Allow the request without interpreting client metadata or credentials."""

        return True
