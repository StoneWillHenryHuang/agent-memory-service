"""Stdio-only MCP reference server over the stable public MemoryEngine facade."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from mcp import stdio_server
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import BaseModel, ValidationError

from portable_memory_engine import (
    AccessDeniedError,
    CapabilityError,
    ConflictError,
    DomainValidationError,
    LifecycleError,
    MemoryEngine,
    MemoryEngineError,
    MemoryScope,
    MemorySubject,
    ProviderError,
    ProviderParseError,
    ProviderUnavailableError,
    StoreError,
    StoreUnavailableError,
    __version__,
)
from portable_memory_engine.integrations.mcp.config import ReferenceMcpConfig
from portable_memory_engine.integrations.mcp.mappers import (
    boundary_for_delete,
    from_add_result,
    from_delete_result,
    from_recall_result,
    to_add_command,
    to_delete_command,
    to_recall_request,
)
from portable_memory_engine.integrations.mcp.schemas import (
    DELETE_TOOL_INPUT,
    DELETE_TOOL_INPUT_SCHEMA,
    AddToolInput,
    AddToolOutput,
    DeleteToolOutput,
    RecallToolInput,
    RecallToolOutput,
)
from portable_memory_engine.integrations.mcp.security import (
    AllowLocalStdioAuthorization,
    McpAuthorizationContext,
    McpAuthorizationHook,
)


@dataclass(frozen=True, slots=True)
class _ReferenceMcpContext:
    engine: MemoryEngine
    authorization: McpAuthorizationHook
    context_id: str


type ReferenceMcpServer = Server[_ReferenceMcpContext]


class _AuthorizationDenied(Exception):
    pass


def _memory_error(error: MemoryEngineError) -> tuple[str, str]:
    if isinstance(error, AccessDeniedError):
        return "access_denied", "access policy denied the operation"
    if isinstance(error, DomainValidationError):
        return "invalid_request", "request violated a memory invariant"
    if isinstance(error, CapabilityError):
        return "capability_unavailable", "operation requires an unavailable capability"
    if isinstance(error, ConflictError):
        return "conflict", "operation conflicted with stored state"
    if isinstance(error, (ProviderUnavailableError, StoreUnavailableError, LifecycleError)):
        return "temporarily_unavailable", "memory service is temporarily unavailable"
    if isinstance(error, ProviderParseError):
        return "provider_output_invalid", "provider output failed validation"
    if isinstance(error, ProviderError):
        return "provider_failure", "provider operation failed"
    if isinstance(error, StoreError):
        return "store_failure", "store operation failed"
    return "memory_engine_failure", "memory operation failed"


def _success(value: BaseModel, message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(text=message)],
        structured_content=value.model_dump(mode="json"),
    )


def _error(*, code: str, message: str, request_id: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(text=f"{code}: {message}; reference={request_id}")],
        is_error=True,
    )


async def _authorize(
    context: _ReferenceMcpContext,
    *,
    tool_name: str,
    request_id: str,
    boundary: MemoryScope | MemorySubject,
) -> None:
    authorization_context = McpAuthorizationContext(
        tool_name=tool_name,
        request_id=request_id,
        context_id=context.context_id,
        boundary=boundary,
    )
    if not await context.authorization.authorize(authorization_context):
        raise _AuthorizationDenied


ADD_TOOL = Tool(
    name="add_memory",
    description="Extract and persist memories inside one explicit scope.",
    input_schema=AddToolInput.model_json_schema(),
    output_schema=AddToolOutput.model_json_schema(),
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
RECALL_TOOL = Tool(
    name="recall_memory",
    description="Recall memories from one explicit scope.",
    input_schema=RecallToolInput.model_json_schema(),
    output_schema=RecallToolOutput.model_json_schema(),
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
DELETE_TOOL = Tool(
    name="delete_memory",
    description="Apply one explicit scoped, subject, or session deletion.",
    input_schema=DELETE_TOOL_INPUT_SCHEMA,
    output_schema=DeleteToolOutput.model_json_schema(),
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
TOOLS = (ADD_TOOL, RECALL_TOOL, DELETE_TOOL)


def create_reference_mcp_server(
    *,
    engine: MemoryEngine,
    config: ReferenceMcpConfig | None = None,
    authorization: McpAuthorizationHook | None = None,
) -> ReferenceMcpServer:
    """Create a tools-only reference server without opening resources."""

    if not isinstance(engine, MemoryEngine):
        raise DomainValidationError("engine must be the public MemoryEngine facade")
    selected_config = config or ReferenceMcpConfig()
    if not isinstance(selected_config, ReferenceMcpConfig):
        raise DomainValidationError("config must be ReferenceMcpConfig")
    selected_authorization = authorization or AllowLocalStdioAuthorization()
    if not isinstance(selected_authorization, McpAuthorizationHook):
        raise DomainValidationError("authorization must implement McpAuthorizationHook")

    @asynccontextmanager
    async def lifespan(_: ReferenceMcpServer) -> AsyncIterator[_ReferenceMcpContext]:
        if selected_config.owns_engine:
            await engine.open()
        try:
            yield _ReferenceMcpContext(
                engine=engine,
                authorization=selected_authorization,
                context_id=uuid4().hex,
            )
        finally:
            if selected_config.owns_engine:
                await engine.close()

    async def list_tools(
        _: ServerRequestContext[_ReferenceMcpContext],
        __: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return ListToolsResult(tools=list(TOOLS))

    async def call_tool(
        request_context: ServerRequestContext[_ReferenceMcpContext],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        request_id = uuid4().hex
        if params.task is not None or params.input_responses is not None or params.request_state:
            return _error(
                code="unsupported_execution",
                message="reference tools support one direct call only",
                request_id=request_id,
            )
        arguments = params.arguments or {}
        context = request_context.lifespan_context
        try:
            if params.name == ADD_TOOL.name:
                add_input = AddToolInput.model_validate(arguments)
                add_command = to_add_command(add_input)
                await _authorize(
                    context,
                    tool_name=params.name,
                    request_id=request_id,
                    boundary=add_command.scope,
                )
                return _success(
                    from_add_result(await context.engine.add(add_command)),
                    "Memory add completed.",
                )
            if params.name == RECALL_TOOL.name:
                recall_input = RecallToolInput.model_validate(arguments)
                recall_request = to_recall_request(recall_input)
                await _authorize(
                    context,
                    tool_name=params.name,
                    request_id=request_id,
                    boundary=recall_request.query.scope,
                )
                return _success(
                    from_recall_result(await context.engine.recall(recall_request)),
                    "Memory recall completed.",
                )
            if params.name == DELETE_TOOL.name:
                delete_input = DELETE_TOOL_INPUT.validate_python(arguments)
                delete_command = to_delete_command(delete_input)
                await _authorize(
                    context,
                    tool_name=params.name,
                    request_id=request_id,
                    boundary=boundary_for_delete(delete_input),
                )
                return _success(
                    from_delete_result(await context.engine.delete(delete_command)),
                    "Memory deletion completed.",
                )
            return _error(
                code="unknown_tool",
                message="tool is not available",
                request_id=request_id,
            )
        except ValidationError:
            return _error(
                code="invalid_tool_input",
                message="arguments did not match the tool schema",
                request_id=request_id,
            )
        except _AuthorizationDenied:
            return _error(
                code="authorization_denied",
                message="tool call was not authorized",
                request_id=request_id,
            )
        except MemoryEngineError as error:
            code, message = _memory_error(error)
            return _error(code=code, message=message, request_id=request_id)
        except Exception:
            return _error(
                code="internal_error",
                message="reference tool failed",
                request_id=request_id,
            )

    server = Server(
        "portable-memory-engine-reference",
        version=__version__,
        description="Non-production stdio tools over the public MemoryEngine facade.",
        instructions=(
            "Use only explicit synthetic or caller-owned scopes. "
            "This server provides no production authentication or deployment policy."
        ),
        lifespan=lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    server.middleware.clear()
    return server


async def run_reference_mcp_stdio(server: ReferenceMcpServer) -> None:
    """Run one reference server connection over process stdio only."""

    if not isinstance(server, Server):
        raise DomainValidationError("server must be a reference MCP Server")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
