"""Generic async Chat Completions adapter using only public HTTP behavior."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import cast

import httpx

from portable_memory_engine.adapters.openai_compatible.config import (
    OpenAICompatibleChatConfig,
    StructuredOutputCapability,
    StructuredOutputMode,
)
from portable_memory_engine.domain import (
    CapabilityError,
    DomainValidationError,
    FrozenJsonObject,
    JsonValue,
    LifecycleError,
    ProviderError,
    ProviderParseError,
    ProviderUnavailableError,
)
from portable_memory_engine.ports import ChatRequest, ChatResponse, TokenUsage

_RETRYABLE_STATUS_CODES = frozenset({429, *range(500, 600)})
_CAPABILITY_STATUS_CODES = frozenset({400, 404, 415, 422})
_RESPONSE_SCHEMA_VERSION = "openai-chat-completions.v1"


async def _sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _HttpResult:
    status_code: int
    headers: Mapping[str, str]
    content: bytes = field(repr=False)


class _ResponseTooLargeError(Exception):
    pass


def _mutable_json(value: JsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json(item) for item in value]
    return value


def _response_format(
    schema: FrozenJsonObject,
    *,
    capability: StructuredOutputCapability,
) -> dict[str, object]:
    if capability is StructuredOutputCapability.JSON_SCHEMA:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "memory_output",
                "strict": True,
                "schema": _mutable_json(schema),
            },
        }
    return {"type": "json_object"}


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            instant = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None
        seconds = (instant.astimezone(UTC) - _now()).total_seconds()
    if not math.isfinite(seconds):
        return None
    if seconds < 0:
        return 0.0
    return seconds


def _json_object(content: bytes) -> Mapping[str, object] | None:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    return cast("Mapping[str, object]", value)


def _is_response_format_rejection(result: _HttpResult) -> bool:
    if result.status_code not in _CAPABILITY_STATUS_CODES:
        return False
    payload = _json_object(result.content)
    if payload is None:
        return False
    error = payload.get("error")
    details = error if isinstance(error, Mapping) else payload
    param = str(details.get("param", "")).lower()
    code = str(details.get("code", "")).lower()
    message = str(details.get("message", "")).lower()
    if "response_format" in param or "response.format" in param:
        return True
    if "response_format" in code and any(
        marker in code for marker in ("unsupported", "invalid", "unknown")
    ):
        return True
    return ("response_format" in message or "response format" in message) and any(
        marker in message for marker in ("not supported", "unsupported", "unknown", "unrecognized")
    )


class OpenAICompatibleChatModel:
    """OpenAI-style ``/chat/completions`` implementation of ``ChatModel``.

    An injected HTTP client remains caller-owned. When no client is injected,
    ``open`` creates one and ``close`` waits for active calls before closing it.
    """

    def __init__(
        self,
        config: OpenAICompatibleChatConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(config, OpenAICompatibleChatConfig):
            raise DomainValidationError("config must be OpenAICompatibleChatConfig")
        if http_client is not None and not isinstance(http_client, httpx.AsyncClient):
            raise DomainValidationError("http_client must be httpx.AsyncClient or None")
        self._config = config
        self._injected_client = http_client
        self._client: httpx.AsyncClient | None = None
        self._condition = asyncio.Condition()
        self._probe_lock = asyncio.Lock()
        self._open = False
        self._closing = False
        self._active_calls = 0
        if config.structured_output_mode is StructuredOutputMode.JSON_SCHEMA:
            self._structured_output_capability = StructuredOutputCapability.JSON_SCHEMA
        elif config.structured_output_mode is StructuredOutputMode.JSON_OBJECT:
            self._structured_output_capability = StructuredOutputCapability.JSON_OBJECT
        else:
            self._structured_output_capability = StructuredOutputCapability.UNKNOWN

    @property
    def structured_output_capability(self) -> StructuredOutputCapability:
        """Return configured or lazily observed response-format support."""

        return self._structured_output_capability

    async def open(self) -> None:
        """Create an owned HTTP client, or attach the injected caller-owned one."""

        async with self._condition:
            while self._closing:
                await self._condition.wait()
            if self._open:
                return
            if self._injected_client is not None:
                if self._injected_client.is_closed:
                    raise LifecycleError("injected chat HTTP client is closed")
                client = self._injected_client
            else:
                client = httpx.AsyncClient(
                    timeout=self._config.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                )
            self._client = client
            self._open = True

    async def close(self) -> None:
        """Wait for active calls and close only an adapter-owned client."""

        async with self._condition:
            while self._closing:
                await self._condition.wait()
            if not self._open:
                return
            self._open = False
            self._closing = True
            try:
                while self._active_calls:
                    await self._condition.wait()
            except asyncio.CancelledError:
                self._open = True
                self._closing = False
                self._condition.notify_all()
                raise
            client = self._client
            owned = self._injected_client is None
            self._client = None

        close_error = False
        if owned and client is not None:
            close_task = asyncio.create_task(client.aclose())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                try:
                    await close_task
                finally:
                    async with self._condition:
                        self._closing = False
                        self._condition.notify_all()
                raise
            except Exception:
                close_error = True
        async with self._condition:
            self._closing = False
            self._condition.notify_all()
        if close_error:
            raise ProviderError("chat HTTP client could not be closed") from None

    async def _acquire_client(self) -> httpx.AsyncClient:
        async with self._condition:
            if not self._open or self._client is None:
                raise LifecycleError("OpenAI-compatible chat model is not open")
            self._active_calls += 1
            return self._client

    async def _release_client(self) -> None:
        async with self._condition:
            self._active_calls -= 1
            if not self._active_calls:
                self._condition.notify_all()

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Complete one sanitized chat request with bounded retry behavior."""

        if not isinstance(request, ChatRequest):
            raise DomainValidationError("chat request must be ChatRequest")
        client = await self._acquire_client()
        try:
            body: dict[str, object] = {
                "model": self._config.model,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
            }
            if request.response_schema is None:
                return await self._send_and_parse(client, body)
            return await self._complete_structured(client, body, request.response_schema)
        finally:
            await self._release_client()

    async def _complete_structured(
        self,
        client: httpx.AsyncClient,
        body: dict[str, object],
        schema: FrozenJsonObject,
    ) -> ChatResponse:
        if self._config.structured_output_mode is not StructuredOutputMode.AUTO:
            capability = self._structured_output_capability
            body["response_format"] = _response_format(schema, capability=capability)
            result = await self._request_with_retries(client, body)
            if _is_response_format_rejection(result):
                raise CapabilityError(
                    operation="chat.complete",
                    capability=capability.value,
                )
            return self._parse_result(result)

        async with self._probe_lock:
            capability = self._structured_output_capability
            if capability is StructuredOutputCapability.UNSUPPORTED:
                raise CapabilityError(
                    operation="chat.complete",
                    capability="structured_output",
                )
            if capability is StructuredOutputCapability.UNKNOWN:
                capability = StructuredOutputCapability.JSON_SCHEMA
            body["response_format"] = _response_format(schema, capability=capability)
            result = await self._request_with_retries(client, body)
            if not _is_response_format_rejection(result):
                self._structured_output_capability = capability
                return self._parse_result(result)
            if capability is StructuredOutputCapability.JSON_SCHEMA:
                capability = StructuredOutputCapability.JSON_OBJECT
                body["response_format"] = _response_format(schema, capability=capability)
                result = await self._request_with_retries(client, body)
                if not _is_response_format_rejection(result):
                    self._structured_output_capability = capability
                    return self._parse_result(result)
            self._structured_output_capability = StructuredOutputCapability.UNSUPPORTED
            raise CapabilityError(
                operation="chat.complete",
                capability="structured_output",
            )

    async def _send_and_parse(
        self,
        client: httpx.AsyncClient,
        body: dict[str, object],
    ) -> ChatResponse:
        result = await self._request_with_retries(client, body)
        return self._parse_result(result)

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        body: dict[str, object],
    ) -> _HttpResult:
        total_attempts = self._config.max_retries + 1
        for attempt in range(total_attempts):
            try:
                result = await self._request_once(client, body)
            except _ResponseTooLargeError:
                raise ProviderParseError(
                    schema_version=_RESPONSE_SCHEMA_VERSION,
                    reason_code="response_too_large",
                ) from None
            except httpx.TransportError:
                if attempt + 1 >= total_attempts:
                    raise ProviderUnavailableError(
                        f"chat provider transport failed after {total_attempts} attempts"
                    ) from None
                await _sleep(self._backoff_delay(attempt))
                continue
            if result.status_code not in _RETRYABLE_STATUS_CODES:
                return result
            if attempt + 1 >= total_attempts:
                raise ProviderUnavailableError(
                    "chat provider remained temporarily unavailable "
                    f"after {total_attempts} attempts (status {result.status_code})"
                ) from None
            retry_after = _retry_after_seconds(result.headers.get("retry-after"))
            delay = self._backoff_delay(attempt) if retry_after is None else retry_after
            await _sleep(min(delay, self._config.max_retry_delay_seconds))
        raise AssertionError("unreachable retry state")

    def _backoff_delay(self, attempt: int) -> float:
        return float(
            min(
                self._config.retry_backoff_seconds * (2**attempt),
                self._config.max_retry_delay_seconds,
            )
        )

    async def _request_once(
        self,
        client: httpx.AsyncClient,
        body: dict[str, object],
    ) -> _HttpResult:
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        async with client.stream(
            "POST",
            f"{self._config.base_url}/chat/completions",
            headers=headers,
            json=body,
            timeout=self._config.timeout_seconds,
            follow_redirects=False,
        ) as response:
            content = bytearray()
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_response_bytes:
                        raise _ResponseTooLargeError
            return _HttpResult(
                status_code=response.status_code,
                headers=dict(response.headers),
                content=bytes(content),
            )

    def _parse_result(self, result: _HttpResult) -> ChatResponse:
        if not 200 <= result.status_code < 300:
            if result.status_code in {401, 403}:
                message = "chat provider authentication or authorization failed"
            else:
                message = f"chat provider rejected the request (status {result.status_code})"
            raise ProviderError(message) from None
        payload = _json_object(result.content)
        if payload is None:
            raise ProviderParseError(
                schema_version=_RESPONSE_SCHEMA_VERSION,
                reason_code="invalid_json",
            )
        try:
            choices = payload["choices"]
            if not isinstance(choices, list) or not choices:
                raise TypeError
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise TypeError
            message = choice["message"]
            if not isinstance(message, Mapping):
                raise TypeError
            content = message["content"]
            model_id = payload["model"]
            if not isinstance(content, str) or not isinstance(model_id, str):
                raise TypeError
            usage = self._parse_usage(payload.get("usage"))
            return ChatResponse(content=content, model_id=model_id, usage=usage)
        except (KeyError, TypeError, DomainValidationError):
            raise ProviderParseError(
                schema_version=_RESPONSE_SCHEMA_VERSION,
                reason_code="invalid_response",
            ) from None

    @staticmethod
    def _parse_usage(value: object) -> TokenUsage:
        if value is None:
            return TokenUsage()
        if not isinstance(value, Mapping):
            raise TypeError
        input_tokens = value.get("prompt_tokens")
        output_tokens = value.get("completion_tokens")
        if (
            isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or isinstance(output_tokens, bool)
            or not isinstance(output_tokens, int)
        ):
            raise TypeError
        total_tokens = value.get("total_tokens", input_tokens + output_tokens)
        if isinstance(total_tokens, bool) or not isinstance(total_tokens, int):
            raise TypeError
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
