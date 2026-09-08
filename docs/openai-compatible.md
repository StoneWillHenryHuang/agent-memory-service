# OpenAI-compatible chat adapter

The optional `openai-compatible` extra implements the public `ChatModel` port
against the OpenAI-style Chat Completions endpoint. It uses ordinary async HTTP
and does not install the OpenAI SDK or another cloud SDK.

## Installation and configuration

From a source checkout:

```bash
python -m pip install ".[openai-compatible]"
```

Every deployment value is required from the caller. The adapter does not read
environment variables or module-global settings:

```python
from portable_memory_engine.adapters.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatModel,
)

model = OpenAICompatibleChatModel(
    OpenAICompatibleChatConfig(
        base_url="https://models.example.com/v1",
        api_key=get_credential(),
        model="caller-selected-model",
        timeout_seconds=30,
        max_retries=2,
    )
)

await model.open()
try:
    # Inject `model` into an application service that consumes `ChatModel`.
    ...
finally:
    await model.close()
```

`base_url` is the API root, including a version path such as `/v1`; the adapter
appends `/chat/completions`. URLs containing embedded credentials, a query, or a
fragment are rejected. The API key, URL, messages, response body, and response
schema are absent from adapter reprs and public error text.

## Request and response contract

Only each conversation message's `role` and `content` are sent. Domain
timestamps and metadata are intentionally not forwarded. The configured model
is sent on every request, and the endpoint's returned model ID is preserved in
the public `ChatResponse`.

OpenAI-style `prompt_tokens`, `completion_tokens`, and `total_tokens` are mapped
to provider-neutral `TokenUsage.input_tokens`, `output_tokens`, and
`total_tokens`. A zero usage value means the endpoint did not report usage; it
is not an estimate.

The maximum response body defaults to 1 MB and is configurable. Missing choices,
missing text/model fields, malformed JSON, invalid usage, and oversized bodies
become sanitized `ProviderParseError` values. Raw response bodies are never
included in those errors.

## Structured-output capability detection

When a `ChatRequest` includes a response schema, the default `auto` mode first
sends the public Chat Completions `json_schema` response format with strict
validation. If the endpoint explicitly identifies `response_format` as
unsupported, the adapter tries the older `json_object` format once and caches
the observed capability for later calls.

If both formats are explicitly rejected, the adapter raises `CapabilityError`;
it does not silently send an unstructured request. Other 4xx responses are not
misclassified as capability failures. Callers that already know an endpoint's
behavior can select `StructuredOutputMode.JSON_SCHEMA` or
`StructuredOutputMode.JSON_OBJECT` and disable fallback.

`json_object` guarantees JSON syntax, not schema conformance. Portable Memory
Engine therefore continues to treat all returned text as untrusted and applies
its versioned strict parser after the model call.

## Retry, cancellation, and lifecycle

The adapter retries transport failures, HTTP 429, and HTTP 5xx responses. The
configured `max_retries` counts retries after the initial attempt. Backoff is
bounded and exponential; a valid `Retry-After` delta or HTTP date takes
precedence, capped by `max_retry_delay_seconds`. Other 4xx responses are not
retried. Exhaustion becomes `ProviderUnavailableError` without the native
exception or provider body.

Task cancellation propagates normally, including during a request or retry
wait. `close()` waits for active calls. A client created by the adapter is
closed by the adapter; an injected `httpx.AsyncClient` remains caller-owned.
The owned client ignores process proxy settings and does not follow redirects.
Deployments that require custom transport, proxy, certificate, or tracing
behavior can inject a configured client and retain its lifecycle.

## Trust boundary

A custom model URL is a data-egress and SSRF boundary. Hosted applications
should select it from trusted deployment configuration, not a normal end-user
request. Deployers remain responsible for TLS policy, secret storage, outbound
network controls, provider data retention/training review, and any logging or
event hooks added to an injected client.
