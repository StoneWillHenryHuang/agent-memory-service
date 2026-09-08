"""Optional stdio-only MCP reference integration."""

from portable_memory_engine.integrations.mcp.config import ReferenceMcpConfig
from portable_memory_engine.integrations.mcp.security import (
    AllowLocalStdioAuthorization,
    McpAuthorizationContext,
    McpAuthorizationHook,
)
from portable_memory_engine.integrations.mcp.server import (
    ReferenceMcpServer,
    create_reference_mcp_server,
    run_reference_mcp_stdio,
)

__all__ = (
    "AllowLocalStdioAuthorization",
    "McpAuthorizationContext",
    "McpAuthorizationHook",
    "ReferenceMcpConfig",
    "ReferenceMcpServer",
    "create_reference_mcp_server",
    "run_reference_mcp_stdio",
)
