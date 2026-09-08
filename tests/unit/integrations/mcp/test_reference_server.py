"""Protocol-level tests for the tools-only MCP reference server."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from mcp import Client
from mcp.types import CallToolResult

from portable_memory_engine import (
    AccessDeniedError,
    CapabilityError,
    ChatResponse,
    ConflictError,
    DefaultPromptProvider,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    DeterministicEmbedder,
    DomainValidationError,
    FixedClock,
    InMemoryMemoryStore,
    LifecycleError,
    MemoryEngine,
    MemoryEngineError,
    MemoryQuery,
    MemoryScope,
    MemorySubject,
    ProviderError,
    ProviderParseError,
    ProviderUnavailableError,
    RecallRequest,
    ScriptedChatModel,
    StoreError,
    StoreUnavailableError,
)
from portable_memory_engine.integrations.mcp import (
    McpAuthorizationContext,
    ReferenceMcpConfig,
    create_reference_mcp_server,
    run_reference_mcp_stdio,
)
from portable_memory_engine.integrations.mcp.mappers import (
    boundary_for_delete,
    to_delete_command,
)
from portable_memory_engine.integrations.mcp.schemas import DELETE_TOOL_INPUT
from portable_memory_engine.integrations.mcp.server import _memory_error

EVENT_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = {
    "tenant_id": "synthetic-tenant",
    "subject_id": "synthetic-subject",
    "namespace": "mcp-reference-test",
}


class RecordingAuthorization:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.contexts: list[McpAuthorizationContext] = []

    async def authorize(self, context: McpAuthorizationContext) -> bool:
        self.contexts.append(context)
        return self.allowed


class FailingAuthorization:
    async def authorize(self, context: McpAuthorizationContext) -> bool:
        raise RuntimeError("synthetic authorization secret")


def _engine() -> MemoryEngine:
    return MemoryEngine(
        store=InMemoryMemoryStore(),
        chat_model=ScriptedChatModel(
            (
                ChatResponse(
                    content='{"schema_version":"fact.v1","facts":["Uses local rail"]}',
                    model_id="mcp-reference-test-v1",
                ),
                ChatResponse(
                    content=(
                        '{"schema_version":"update.v1","operations":['
                        '{"event":"add","memory_id":null,"content":"Uses local rail"}]}'
                    ),
                    model_id="mcp-reference-test-v1",
                ),
            )
        ),
        embedder=DeterministicEmbedder(dimension=8),
        prompt_provider=DefaultPromptProvider(),
        clock=FixedClock(EVENT_TIME),
    )


def _add_arguments() -> dict[str, object]:
    return {
        "scope": SCOPE,
        "messages": [{"role": "user", "content": "I use local rail"}],
        "idempotency_key": "mcp-add-1",
        "event_timestamp": EVENT_TIME.isoformat(),
        "kinds": ["fact"],
        "session_id": "synthetic-session-1",
    }


def _text(result: CallToolResult) -> str:
    return " ".join(item.text for item in result.content if hasattr(item, "text"))


def test_add_recall_delete_use_only_public_facade() -> None:
    engine = _engine()
    server = create_reference_mcp_server(engine=engine)

    async def scenario() -> None:
        async with Client(server) as client:
            added = await client.call_tool("add_memory", _add_arguments())
            recalled = await client.call_tool(
                "recall_memory",
                {"scope": SCOPE, "kinds": ["fact"]},
            )
            assert isinstance(recalled.structured_content, dict)
            memory_id = recalled.structured_content["items"][0]["id"]
            deleted = await client.call_tool(
                "delete_memory",
                {
                    "target": "memory_id",
                    "scope": SCOPE,
                    "memory_id": memory_id,
                    "idempotency_key": "mcp-delete-1",
                    "payload_digest": "mcp-delete-digest-1",
                    "event_timestamp": "2026-01-01T00:01:00Z",
                },
            )
            empty = await client.call_tool("recall_memory", {"scope": SCOPE})

        assert not added.is_error
        assert added.structured_content["status"] == "succeeded"
        assert not recalled.is_error
        assert recalled.structured_content["items"][0]["content"] == "Uses local rail"
        assert recalled.structured_content["items"][0]["session_id"] == "synthetic-session-1"
        assert "source" not in recalled.structured_content["items"][0]
        assert deleted.structured_content["hard_deleted_count"] == 1
        assert empty.structured_content["items"] == []

        with pytest.raises(LifecycleError):
            await engine.recall(
                RecallRequest(
                    MemoryQuery(
                        scope=MemoryScope(
                            tenant_id="synthetic-tenant",
                            subject_id="synthetic-subject",
                            namespace="mcp-reference-test",
                        )
                    )
                )
            )

    asyncio.run(scenario())


def test_server_advertises_exact_tools_and_no_other_capability() -> None:
    server = create_reference_mcp_server(engine=_engine())
    capabilities = server.get_capabilities()

    async def scenario() -> None:
        async with Client(server) as client:
            listed = await client.list_tools(cache_mode="refresh")

        assert [tool.name for tool in listed.tools] == [
            "add_memory",
            "recall_memory",
            "delete_memory",
        ]
        rendered = json.dumps(
            [tool.model_dump(by_alias=True, mode="json") for tool in listed.tools],
            sort_keys=True,
        ).lower()
        for forbidden in (
            "api_key",
            "authorization",
            "client_id",
            "client registry",
            "http://",
            "https://",
            '"source"',
        ):
            assert forbidden not in rendered
        assert all(tool.annotations is not None for tool in listed.tools)
        assert listed.tools[0].annotations is not None
        assert listed.tools[1].annotations is not None
        assert listed.tools[2].annotations is not None
        assert listed.tools[0].annotations.idempotent_hint is True
        assert listed.tools[1].annotations.read_only_hint is True
        assert listed.tools[2].annotations.destructive_hint is True

    assert capabilities.tools is not None
    assert capabilities.resources is None
    assert capabilities.prompts is None
    assert capabilities.logging is None
    assert server.middleware == []
    asyncio.run(scenario())


def test_context_is_stable_within_connection_and_isolated_between_connections() -> None:
    authorization = RecordingAuthorization(allowed=True)
    server = create_reference_mcp_server(engine=_engine(), authorization=authorization)

    async def scenario() -> None:
        async with Client(server) as first:
            await first.call_tool("recall_memory", {"scope": SCOPE})
            await first.call_tool("recall_memory", {"scope": SCOPE})
        async with Client(server) as second:
            await second.call_tool("recall_memory", {"scope": SCOPE})

    asyncio.run(scenario())

    assert len(authorization.contexts) == 3
    first, second, third = authorization.contexts
    assert first.context_id == second.context_id
    assert third.context_id != first.context_id
    assert len({first.request_id, second.request_id, third.request_id}) == 3
    assert first.tool_name == "recall_memory"
    assert first.boundary == MemoryScope(**SCOPE)
    assert "synthetic-subject" not in repr(first)


def test_denied_invalid_and_unexpected_calls_never_echo_sensitive_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")

    async def scenario() -> tuple[
        CallToolResult,
        CallToolResult,
        CallToolResult,
        CallToolResult,
        CallToolResult,
    ]:
        denied_server = create_reference_mcp_server(
            engine=_engine(),
            authorization=RecordingAuthorization(allowed=False),
        )
        failed_server = create_reference_mcp_server(
            engine=_engine(),
            authorization=FailingAuthorization(),
        )
        async with Client(denied_server) as denied_client:
            denied = await denied_client.call_tool("add_memory", _add_arguments())
        async with Client(failed_server) as failed_client:
            failed = await failed_client.call_tool(
                "recall_memory",
                {
                    "scope": SCOPE,
                    "mode": "semantic",
                    "semantic_text": "private-query-marker",
                },
            )
        invalid_arguments = _add_arguments()
        invalid_arguments["api_key"] = "private-api-key-marker"
        async with Client(create_reference_mcp_server(engine=_engine())) as client:
            invalid = await client.call_tool("add_memory", invalid_arguments)
            unknown = await client.call_tool("not_a_tool", {"secret": "private-tool-marker"})
            invalid_kind = await client.call_tool(
                "recall_memory",
                {"scope": SCOPE, "kinds": ["private-kind-marker"]},
            )
        return denied, failed, invalid, unknown, invalid_kind

    denied, failed, invalid, unknown, invalid_kind = asyncio.run(scenario())

    assert denied.is_error
    assert "authorization_denied" in _text(denied)
    assert failed.is_error
    assert "internal_error" in _text(failed)
    assert invalid.is_error
    assert "invalid_tool_input" in _text(invalid)
    assert unknown.is_error
    assert "unknown_tool" in _text(unknown)
    assert invalid_kind.is_error
    assert "invalid_request" in _text(invalid_kind)
    rendered = " ".join(
        (
            _text(denied),
            _text(failed),
            _text(invalid),
            _text(unknown),
            _text(invalid_kind),
            *(record.getMessage() for record in caplog.records),
        )
    )
    for secret in (
        "I use local rail",
        "synthetic authorization secret",
        "private-query-marker",
        "private-api-key-marker",
        "private-tool-marker",
        "private-kind-marker",
    ):
        assert secret not in rendered


def test_direct_calls_only_and_delete_subject_boundary_are_explicit() -> None:
    server = create_reference_mcp_server(engine=_engine())
    scope_input = DELETE_TOOL_INPUT.validate_python(
        {
            "target": "scope",
            "scope": SCOPE,
            "kinds": ["fact"],
            "idempotency_key": "scope-delete-1",
            "payload_digest": "scope-delete-digest-1",
            "event_timestamp": EVENT_TIME.isoformat(),
        }
    )
    subject_input = DELETE_TOOL_INPUT.validate_python(
        {
            "target": "subject",
            "subject": {
                "tenant_id": "synthetic-tenant",
                "subject_id": "synthetic-subject",
            },
            "idempotency_key": "subject-delete-1",
            "payload_digest": "subject-delete-digest-1",
            "event_timestamp": EVENT_TIME.isoformat(),
        }
    )
    session_input = DELETE_TOOL_INPUT.validate_python(
        {
            "target": "session",
            "scope": SCOPE,
            "session_id": "synthetic-session-1",
            "idempotency_key": "session-delete-1",
            "payload_digest": "session-delete-digest-1",
            "event_timestamp": EVENT_TIME.isoformat(),
        }
    )

    async def scenario() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(
                "recall_memory",
                {"scope": SCOPE},
                request_state="unsupported-state",
            )

    unsupported = asyncio.run(scenario())

    assert unsupported.is_error
    assert "unsupported_execution" in _text(unsupported)
    assert boundary_for_delete(subject_input) == MemorySubject(
        tenant_id="synthetic-tenant",
        subject_id="synthetic-subject",
    )
    assert isinstance(to_delete_command(scope_input), DeleteScopeCommand)
    assert isinstance(to_delete_command(subject_input), DeleteSubjectCommand)
    assert isinstance(to_delete_command(session_input), DeleteSessionCommand)


@pytest.mark.parametrize(
    ("error", "expected_code"),
    (
        (AccessDeniedError(operation="recall"), "access_denied"),
        (DomainValidationError("private-error-marker"), "invalid_request"),
        (
            CapabilityError(operation="recall", capability="private-error-marker"),
            "capability_unavailable",
        ),
        (ConflictError(memory_id="private-error-marker"), "conflict"),
        (ProviderUnavailableError("private-error-marker"), "temporarily_unavailable"),
        (StoreUnavailableError("private-error-marker"), "temporarily_unavailable"),
        (LifecycleError("private-error-marker"), "temporarily_unavailable"),
        (
            ProviderParseError(schema_version="private-error-marker"),
            "provider_output_invalid",
        ),
        (ProviderError("private-error-marker"), "provider_failure"),
        (StoreError("private-error-marker"), "store_failure"),
        (MemoryEngineError("private-error-marker"), "memory_engine_failure"),
    ),
)
def test_memory_errors_have_stable_sanitized_mcp_codes(
    error: MemoryEngineError,
    expected_code: str,
) -> None:
    code, message = _memory_error(error)

    assert code == expected_code
    assert "private-error-marker" not in message


def test_factory_and_caller_owned_lifecycle_are_explicit() -> None:
    engine = _engine()

    async def scenario() -> None:
        await engine.open()
        server = create_reference_mcp_server(
            engine=engine,
            config=ReferenceMcpConfig(owns_engine=False),
        )
        async with Client(server) as client:
            recalled = await client.call_tool("recall_memory", {"scope": SCOPE})
        assert not recalled.is_error
        await engine.recall(RecallRequest(MemoryQuery(scope=MemoryScope(**SCOPE))))
        await engine.close()

    asyncio.run(scenario())

    with pytest.raises(DomainValidationError):
        ReferenceMcpConfig(owns_engine="yes")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        create_reference_mcp_server(engine=object())  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        create_reference_mcp_server(
            engine=_engine(),
            config=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        create_reference_mcp_server(
            engine=_engine(),
            authorization=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        asyncio.run(run_reference_mcp_stdio(object()))  # type: ignore[arg-type]
