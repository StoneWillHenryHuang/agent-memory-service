"""Network-free tests for the generic OpenAI-style chat adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import cast

import httpx
import pytest

import portable_memory_engine.adapters.openai_compatible.model as model_module
from portable_memory_engine.adapters.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatModel,
    StructuredOutputCapability,
    StructuredOutputMode,
)
from portable_memory_engine.domain import (
    CapabilityError,
    ConversationMessage,
    DomainValidationError,
    FrozenJsonObject,
    LifecycleError,
    ProviderError,
    ProviderParseError,
    ProviderUnavailableError,
)
from portable_memory_engine.ports import ChatModel, ChatRequest

type Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


def _config(**overrides: object) -> OpenAICompatibleChatConfig:
    values: dict[str, object] = {
        "base_url": "https://models.example.com/v1",
        "api_key": "synthetic-secret-token",
        "model": "caller-model-v1",
        "timeout_seconds": 5.0,
        "max_retries": 2,
        "retry_backoff_seconds": 0.5,
        "max_retry_delay_seconds": 10.0,
    }
    values.update(overrides)
    return OpenAICompatibleChatConfig(**values)  # type: ignore[arg-type]


def _request(*, structured: bool = False) -> ChatRequest:
    schema = FrozenJsonObject(
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
    )
    return ChatRequest(
        (
            ConversationMessage(role="system", content="Return synthetic output."),
            ConversationMessage(role="user", content="Synthetic input."),
        ),
        response_schema=schema if structured else None,
    )


def _success(
    request: httpx.Request,
    *,
    content: str = '{"answer":"ok"}',
    usage: object = None,
) -> httpx.Response:
    payload: dict[str, object] = {
        "id": "chatcmpl-synthetic",
        "object": "chat.completion",
        "model": "served-model-v2",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(200, json=payload, request=request)


async def _run_with_client(
    handler: Handler,
    operation: Callable[[OpenAICompatibleChatModel], Awaitable[None]],
    *,
    config: OpenAICompatibleChatConfig | None = None,
) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleChatModel(config or _config(), http_client=client)
    try:
        await operation(model)
    finally:
        await model.close()
        await client.aclose()


def test_config_is_explicit_validated_and_secret_safe() -> None:
    config = _config()
    raw_result = model_module._HttpResult(
        status_code=200,
        headers={},
        content=b"synthetic-private-model-output",
    )

    assert config.base_url == "https://models.example.com/v1"
    assert config.model == "caller-model-v1"
    assert "synthetic-secret-token" not in repr(config)
    assert "models.example.com" not in repr(config)
    assert "synthetic-private-model-output" not in repr(raw_result)

    for overrides in (
        {"base_url": "models.example.com/v1"},
        {"base_url": "https://user:password@models.example.com/v1"},
        {"api_key": "bad\nkey"},
        {"model": "bad model"},
        {"timeout_seconds": 0},
        {"max_retries": 11},
        {"max_response_bytes": 0},
        {"structured_output_mode": "auto"},
    ):
        with pytest.raises(DomainValidationError):
            _config(**overrides)


def test_openai_style_request_maps_schema_usage_and_lifecycle() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _success(
            request,
            usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        )

    async def operation(model: OpenAICompatibleChatModel) -> None:
        assert isinstance(model, ChatModel)
        assert model.structured_output_capability.value == "unknown"
        with pytest.raises(LifecycleError):
            await model.complete(_request(structured=True))
        await model.open()
        await model.open()
        response = await model.complete(_request(structured=True))
        await model.close()
        await model.close()

        assert response.content == '{"answer":"ok"}'
        assert response.model_id == "served-model-v2"
        assert response.usage.input_tokens == 7
        assert response.usage.output_tokens == 3
        assert response.usage.total_tokens == 10
        assert model.structured_output_capability.value == "json_schema"
        with pytest.raises(LifecycleError):
            await model.complete(_request())

    asyncio.run(_run_with_client(handler, operation))

    assert len(captured) == 1
    request = captured[0]
    assert request.url == "https://models.example.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer synthetic-secret-token"
    body = cast("dict[str, object]", json.loads(request.content))
    assert body["model"] == "caller-model-v1"
    assert body["messages"] == [
        {"role": "system", "content": "Return synthetic output."},
        {"role": "user", "content": "Synthetic input."},
    ]
    response_format = cast("dict[str, object]", body["response_format"])
    assert response_format["type"] == "json_schema"
    json_schema = cast("dict[str, object]", response_format["json_schema"])
    assert json_schema["strict"] is True
    assert cast("dict[str, object]", json_schema["schema"])["type"] == "object"


def test_auto_capability_detection_falls_back_once_and_caches_json_object() -> None:
    formats: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = cast("dict[str, object]", json.loads(request.content))
        response_format = cast("dict[str, object]", body["response_format"])
        formats.append(cast("str", response_format["type"]))
        if response_format["type"] == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "response_format json_schema is not supported",
                        "param": "response_format",
                        "code": "unsupported_response_format",
                    }
                },
                request=request,
            )
        return _success(request)

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        await model.complete(_request(structured=True))
        assert model.structured_output_capability is StructuredOutputCapability.JSON_OBJECT
        await model.complete(_request(structured=True))

    asyncio.run(_run_with_client(handler, operation))

    assert formats == ["json_schema", "json_object", "json_object"]


def test_unsupported_structured_output_is_explicit_and_cached() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            422,
            json={"error": {"param": "response_format", "message": "unsupported"}},
            request=request,
        )

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        for _ in range(2):
            with pytest.raises(CapabilityError, match="structured_output"):
                await model.complete(_request(structured=True))
        assert model.structured_output_capability is StructuredOutputCapability.UNSUPPORTED

    asyncio.run(_run_with_client(handler, operation))

    assert calls == 2


def test_explicit_response_format_mode_does_not_silently_fallback() -> None:
    formats: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = cast("dict[str, object]", json.loads(request.content))
        response_format = cast("dict[str, object]", body["response_format"])
        formats.append(cast("str", response_format["type"]))
        return httpx.Response(
            400,
            json={"error": {"param": "response_format", "message": "unsupported"}},
            request=request,
        )

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        with pytest.raises(CapabilityError, match="json_schema"):
            await model.complete(_request(structured=True))

    config = _config(structured_output_mode=StructuredOutputMode.JSON_SCHEMA)
    asyncio.run(_run_with_client(handler, operation, config=config))

    assert formats == ["json_schema"]


def test_retry_respects_retry_after_then_uses_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = [429, 503, 200]
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        if status == 200:
            return _success(request)
        headers = {"Retry-After": "2"} if status == 429 else {}
        return httpx.Response(status, headers=headers, request=request)

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(model_module, "_sleep", fake_sleep)

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        assert (await model.complete(_request())).content == '{"answer":"ok"}'

    asyncio.run(_run_with_client(handler, operation))

    assert delays == [2.0, 1.0]


def test_retry_after_http_date_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    delays: list[float] = []
    now = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                503,
                headers={
                    "Retry-After": format_datetime(
                        datetime(2026, 7, 31, 8, 0, 4, tzinfo=UTC),
                        usegmt=True,
                    )
                },
                request=request,
            )
        return _success(request)

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(model_module, "_now", lambda: now)
    monkeypatch.setattr(model_module, "_sleep", fake_sleep)

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        await model.complete(_request())

    asyncio.run(_run_with_client(handler, operation))

    assert delays == [4.0]


def test_transport_errors_retry_and_exhaustion_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(
            "synthetic transport detail with synthetic-secret-token",
            request=request,
        )

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(model_module, "_sleep", fake_sleep)

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        with pytest.raises(ProviderUnavailableError) as captured:
            await model.complete(_request())
        rendered = f"{captured.value!s} {captured.value!r}"
        assert "synthetic-secret-token" not in rendered
        assert "Synthetic input" not in rendered

    asyncio.run(_run_with_client(handler, operation))

    assert calls == 3
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize(
    ("response", "reason_code"),
    [
        (lambda request: httpx.Response(200, content=b"not-json", request=request), "invalid_json"),
        (
            lambda request: httpx.Response(200, json={"model": "model"}, request=request),
            "invalid_response",
        ),
        (
            lambda request: _success(
                request,
                usage={"prompt_tokens": "7", "completion_tokens": 3},
            ),
            "invalid_response",
        ),
        (lambda request: _success(request, content="bad\x00content"), "invalid_response"),
    ],
)
def test_invalid_responses_raise_sanitized_parse_errors(
    response: Callable[[httpx.Request], httpx.Response],
    reason_code: str,
) -> None:
    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        with pytest.raises(ProviderParseError) as captured:
            await model.complete(_request())
        assert captured.value.reason_code == reason_code
        assert "Synthetic input" not in str(captured.value)

    asyncio.run(_run_with_client(response, operation))


def test_response_size_is_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _success(request, content="x" * 100)

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        with pytest.raises(ProviderParseError) as captured:
            await model.complete(_request())
        assert captured.value.reason_code == "response_too_large"

    asyncio.run(
        _run_with_client(
            handler,
            operation,
            config=_config(max_response_bytes=64),
        )
    )


def test_non_retryable_error_does_not_expose_response_or_request() -> None:
    leaked_response = "synthetic response detail containing synthetic-secret-token"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": leaked_response}},
            request=request,
        )

    async def operation(model: OpenAICompatibleChatModel) -> None:
        await model.open()
        with pytest.raises(ProviderError) as captured:
            await model.complete(_request())
        rendered = f"{captured.value!s} {captured.value!r}"
        assert leaked_response not in rendered
        assert "synthetic-secret-token" not in rendered
        assert "Synthetic input" not in rendered

    asyncio.run(_run_with_client(handler, operation))


def test_cancellation_propagates_and_releases_lifecycle() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        never = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await never.wait()
            return _success(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = OpenAICompatibleChatModel(_config(), http_client=client)
        await model.open()
        task = asyncio.create_task(model.complete(_request()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await model.close()

        assert not client.is_closed
        await client.aclose()

    asyncio.run(scenario())


def test_owned_client_can_be_closed_and_reopened_without_network() -> None:
    async def scenario() -> None:
        model = OpenAICompatibleChatModel(_config())
        await model.open()
        await model.close()
        await model.open()
        await model.close()

    asyncio.run(scenario())
