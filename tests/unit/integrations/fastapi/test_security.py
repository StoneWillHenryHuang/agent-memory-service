"""Host, CORS, authorization, request-ID, and redaction security tests."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from portable_memory_engine import DomainValidationError
from portable_memory_engine.integrations.fastapi import (
    AuthorizationContext,
    LocalhostOnlyAuthorization,
    ReferenceServiceConfig,
    create_reference_app,
)
from tests.unit.integrations.fastapi.test_reference_app import _add_payload, _client, _engine


class RecordingAuthorization:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.contexts: list[AuthorizationContext] = []

    async def authorize(self, context: AuthorizationContext) -> bool:
        self.contexts.append(context)
        return self.allowed


class FailingAuthorization:
    async def authorize(self, context: AuthorizationContext) -> bool:
        raise RuntimeError("synthetic authorization secret")


def test_default_authorization_rejects_remote_clients_but_health_is_content_free() -> None:
    app = create_reference_app(engine=_engine())

    with _client(app, host="198.51.100.7") as client:
        health = client.get("/health")
        denied = client.post("/v1/memories/add", json=_add_payload())

    assert health.status_code == 200
    assert health.json() == {"status": "healthy"}
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "authorization_denied"
    assert denied.json()["error"]["request_id"] == denied.headers["x-request-id"]


def test_injected_authorization_receives_redacted_context_repr() -> None:
    authorization = RecordingAuthorization(allowed=False)
    app = create_reference_app(engine=_engine(), authorization=authorization)

    with _client(app, host="198.51.100.7") as client:
        response = client.post(
            "/v1/memories/add",
            json=_add_payload(),
            headers={"Authorization": "Bearer synthetic-private-token"},
        )

    assert response.status_code == 403
    assert len(authorization.contexts) == 1
    assert authorization.contexts[0].client_host == "198.51.100.7"
    assert authorization.contexts[0].authorization == "Bearer synthetic-private-token"
    assert "synthetic-private-token" not in repr(authorization.contexts[0])
    assert "synthetic-private-token" not in response.text


def test_unexpected_auth_failure_never_exposes_native_error_or_logs_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    app = create_reference_app(engine=_engine(), authorization=FailingAuthorization())

    with _client(app) as client:
        response = client.post(
            "/v1/memories/add",
            json=_add_payload(),
            headers={"Authorization": "Bearer do-not-log-this-token"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    rendered = response.text + " ".join(record.getMessage() for record in caplog.records)
    assert "synthetic authorization secret" not in rendered
    assert "do-not-log-this-token" not in rendered
    assert "I use local rail" not in rendered


def test_successful_requests_do_not_log_content_query_or_api_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    app = create_reference_app(engine=_engine())
    payload = _add_payload()
    payload["messages"] = [{"role": "user", "content": "private-message-marker"}]

    with _client(app) as client:
        added = client.post(
            "/v1/memories/add",
            json=payload,
            headers={"Authorization": "Bearer private-api-key-marker"},
        )
        recalled = client.post(
            "/v1/memories/recall",
            json={
                "scope": payload["scope"],
                "mode": "semantic",
                "semantic_text": "private-query-marker",
            },
        )

    assert added.status_code == 200
    assert recalled.status_code == 200
    logs = " ".join(record.getMessage() for record in caplog.records)
    for secret in (
        "private-message-marker",
        "Uses local rail",
        "private-query-marker",
        "private-api-key-marker",
    ):
        assert secret not in logs


def test_trusted_host_and_cors_defaults_are_restrictive() -> None:
    app = create_reference_app(engine=_engine())

    with TestClient(
        app,
        base_url="http://untrusted.example",
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as untrusted:
        rejected_host = untrusted.get("/health")
    with _client(app) as client:
        cors = client.get("/health", headers={"Origin": "https://untrusted.example"})
        preflight = client.options(
            "/v1/memories/add",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert rejected_host.status_code == 400
    assert "access-control-allow-origin" not in cors.headers
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers


def test_explicit_local_cors_origin_is_allowed_without_credentials() -> None:
    config = ReferenceServiceConfig(cors_origins=("http://localhost:3000",))
    app = create_reference_app(
        engine=_engine(),
        config=config,
    )

    with _client(app) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in response.headers
    assert "localhost" not in repr(config)


@pytest.mark.parametrize(
    "config",
    [
        {"allowed_hosts": ("*",)},
        {"allowed_hosts": ("*.example.com",)},
        {"allowed_hosts": ()},
        {"cors_origins": ("*",)},
        {"cors_origins": ("https://user:password@example.com",)},
        {"cors_origins": ("https://example.com:invalid",)},
        {"cors_origins": ("https://example.com/path",)},
        {"cors_origins": ("https://example.com", "https://example.com")},
        {"owns_engine": "yes"},
    ],
)
def test_reference_config_rejects_unsafe_ambiguous_defaults(config: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        ReferenceServiceConfig(**config)  # type: ignore[arg-type]


def test_localhost_authorization_accepts_only_ip_loopback_values() -> None:
    authorization = LocalhostOnlyAuthorization()

    async def scenario() -> None:
        for loopback_host in ("127.0.0.1", "::1"):
            assert await authorization.authorize(
                AuthorizationContext("POST", "/", loopback_host, "id")
            )
        for denied_host in (None, "localhost", "198.51.100.7"):
            assert not await authorization.authorize(
                AuthorizationContext("POST", "/", denied_host, "id")
            )

    asyncio.run(scenario())
