"""End-to-end HTTP tests over the public facade and synthetic adapters."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portable_memory_engine import (
    ChatResponse,
    DefaultPromptProvider,
    DeleteScopeCommand,
    DeleteSessionCommand,
    DeleteSubjectCommand,
    DeterministicEmbedder,
    FixedClock,
    InMemoryMemoryStore,
    LifecycleError,
    MemoryEngine,
    MemoryKind,
    MemoryQuery,
    MemoryScope,
    RecallRequest,
    ScriptedChatModel,
)
from portable_memory_engine.integrations.fastapi import create_reference_app
from portable_memory_engine.integrations.fastapi.mappers import to_delete_command
from portable_memory_engine.integrations.fastapi.schemas import (
    DeleteScopeRequest,
    DeleteSessionRequest,
    DeleteSubjectRequest,
    ScopeSchema,
    SubjectSchema,
)

EVENT_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = {
    "tenant_id": "synthetic-tenant",
    "subject_id": "synthetic-subject",
    "namespace": "reference-test",
}


def _engine() -> MemoryEngine:
    return MemoryEngine(
        store=InMemoryMemoryStore(),
        chat_model=ScriptedChatModel(
            (
                ChatResponse(
                    content='{"schema_version":"fact.v1","facts":["Uses local rail"]}',
                    model_id="reference-test-v1",
                ),
                ChatResponse(
                    content=(
                        '{"schema_version":"update.v1","operations":['
                        '{"event":"add","memory_id":null,"content":"Uses local rail"}]}'
                    ),
                    model_id="reference-test-v1",
                ),
            )
        ),
        embedder=DeterministicEmbedder(dimension=8),
        prompt_provider=DefaultPromptProvider(),
        clock=FixedClock(EVENT_TIME),
    )


def _add_payload() -> dict[str, object]:
    return {
        "scope": SCOPE,
        "messages": [{"role": "user", "content": "I use local rail"}],
        "idempotency_key": "reference-add-1",
        "event_timestamp": EVENT_TIME.isoformat(),
        "kinds": ["fact"],
        "source": "reference-test",
        "session_id": "reference-session-1",
    }


def _client(app: FastAPI, *, host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        app,
        base_url="http://localhost",
        client=(host, 50000),
        raise_server_exceptions=False,
    )


def test_add_recall_delete_and_health_cover_only_public_use_cases() -> None:
    engine = _engine()
    app = create_reference_app(engine=engine)

    with _client(app) as client:
        health = client.get("/health", headers={"X-Request-ID": "caller-controlled-id"})
        added = client.post("/v1/memories/add", json=_add_payload())
        recalled = client.post(
            "/v1/memories/recall",
            json={"scope": SCOPE, "kinds": ["fact"]},
        )
        memory_id = recalled.json()["items"][0]["id"]
        deleted = client.post(
            "/v1/memories/delete",
            json={
                "target": "memory_id",
                "scope": SCOPE,
                "memory_id": memory_id,
                "idempotency_key": "reference-delete-1",
                "payload_digest": "reference-delete-digest-1",
                "event_timestamp": "2026-01-01T00:01:00Z",
            },
        )
        empty = client.post("/v1/memories/recall", json={"scope": SCOPE})

    assert health.json() == {"status": "healthy"}
    assert added.status_code == 200
    assert added.json()["status"] == "succeeded"
    assert added.json()["kind_results"][0]["kind"] == "fact"
    assert [item["content"] for item in recalled.json()["items"]] == ["Uses local rail"]
    assert recalled.json()["count_precision"] == "exact"
    assert deleted.json()["hard_deleted_count"] == 1
    assert deleted.json()["deleted_count"] == 1
    assert empty.json()["items"] == []
    assert len(health.headers["x-request-id"]) == 32
    assert health.headers["x-request-id"] != "caller-controlled-id"
    assert health.headers["x-request-id"] != added.headers["x-request-id"]

    async def closed() -> None:
        with pytest.raises(LifecycleError):
            await engine.recall(
                RecallRequest(
                    MemoryQuery(
                        scope=MemoryScope(
                            tenant_id="synthetic-tenant",
                            subject_id="synthetic-subject",
                            namespace="reference-test",
                        )
                    )
                )
            )

    asyncio.run(closed())


def test_http_schema_rejects_unknown_and_domain_invalid_values_without_echo() -> None:
    app = create_reference_app(engine=_engine())
    payload = _add_payload()
    payload["unknown_field"] = "do-not-reflect-value"

    with _client(app) as client:
        schema_error = client.post("/v1/memories/add", json=payload)
        domain_error = client.post(
            "/v1/memories/recall",
            json={
                "scope": {
                    "tenant_id": "synthetic-tenant",
                    "subject_id": "private subject text",
                    "namespace": "reference-test",
                }
            },
        )

    assert schema_error.status_code == 422
    assert schema_error.json()["error"]["code"] == "invalid_http_request"
    assert "do-not-reflect-value" not in schema_error.text
    assert domain_error.status_code == 422
    assert domain_error.json()["error"]["code"] == "invalid_request"
    assert "private subject text" not in domain_error.text
    assert schema_error.json()["error"]["request_id"] == schema_error.headers["x-request-id"]


def test_openapi_contains_only_reference_routes_and_localhost_server() -> None:
    app = create_reference_app(engine=_engine())
    schema = app.openapi()

    assert set(schema["paths"]) == {
        "/health",
        "/v1/memories/add",
        "/v1/memories/delete",
        "/v1/memories/recall",
    }
    assert schema["servers"] == [
        {"url": "http://127.0.0.1:8000", "description": "Local reference server"}
    ]
    assert {getattr(route, "path", None) for route in app.routes} == set(schema["paths"])
    rendered = str(schema).lower()
    assert "admin" not in rendered
    assert "redis" not in rendered
    assert "client registry" not in rendered


def test_unknown_route_and_method_use_bounded_original_errors() -> None:
    app = create_reference_app(engine=_engine())

    with _client(app) as client:
        missing = client.get("/not-a-route")
        wrong_method = client.get("/v1/memories/add")

    assert missing.status_code == 404
    assert missing.json() == {
        "error": {
            "code": "route_not_found",
            "message": "route was not found",
            "request_id": missing.headers["x-request-id"],
        }
    }
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"


def test_delete_transport_variants_map_to_public_commands() -> None:
    scope = ScopeSchema(**SCOPE)
    idempotency_key = "reference-delete-variant"
    payload_digest = "reference-delete-variant-digest"

    scope_command = to_delete_command(
        DeleteScopeRequest(
            target="scope",
            scope=scope,
            kinds=["fact"],
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            event_timestamp=EVENT_TIME,
        )
    )
    subject_command = to_delete_command(
        DeleteSubjectRequest(
            target="subject",
            subject=SubjectSchema(
                tenant_id="synthetic-tenant",
                subject_id="synthetic-subject",
            ),
            kinds=["profile"],
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            event_timestamp=EVENT_TIME,
        )
    )
    session_command = to_delete_command(
        DeleteSessionRequest(
            target="session",
            scope=scope,
            session_id="synthetic-session",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            event_timestamp=EVENT_TIME,
        )
    )

    assert isinstance(scope_command, DeleteScopeCommand)
    assert scope_command.kinds == frozenset({MemoryKind.FACT})
    assert isinstance(subject_command, DeleteSubjectCommand)
    assert subject_command.subject.subject_id == "synthetic-subject"
    assert isinstance(session_command, DeleteSessionCommand)
    assert session_command.session_id == "synthetic-session"
