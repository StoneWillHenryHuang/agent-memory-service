# ADR 0008: Generic OpenAI-compatible chat over public HTTP

- Status: Accepted
- Date: 2026-07-31

## Context

The application layer consumes the provider-neutral `ChatModel` port. The first
network adapter must work with public OpenAI-style endpoints without embedding
a preset gateway URL, model, header, environment setting, or cloud SDK in the
base package. Endpoint support for `response_format` varies, and model content,
credentials, and native errors may be sensitive.

## Decision

Ship `OpenAICompatibleChatModel` in an independent `openai-compatible` extra
using HTTPX's public async API. The caller supplies the API root, bearer API key,
model, timeout, retry limits, response-size bound, and structured-output mode.
Construction and import perform no I/O or environment lookup.

The adapter calls `/chat/completions`, translates only neutral message fields,
maps token counts to `TokenUsage`, and returns no native HTTP object. Automatic
structured-output detection is lazy: try strict `json_schema`, fall back once to
`json_object` only after an explicit response-format rejection, then cache the
observed support. Rejecting both formats is a capability failure, not permission
to remove the schema silently.

Transport errors, 429, and 5xx are retried with bounded exponential delay and
`Retry-After` support. Cancellation is not converted into a provider failure.
Owned and injected HTTP clients have explicit, different lifecycle ownership.
Public errors contain bounded status/reason identifiers only, never the API key,
messages, full request, or response body.

## Consequences

- The base install remains dependency-free and provider-neutral.
- Compatible endpoints need only public Chat Completions HTTP behavior; no
  provider SDK is required.
- JSON-object fallback still relies on the existing strict application parser
  for schema validation.
- Capability discovery may add one rejected call on first structured use.
- Callers that need custom authentication, proxy, certificate, or transport
  behavior must inject an HTTP client or provide another adapter.
- The configurable base URL is an explicit egress/SSRF boundary that hosted
  integrations must keep outside ordinary end-user control.

## Rejected alternatives

### Add HTTP or provider values to the core port

Rejected because HTTP clients, headers, status codes, and native response
objects would couple application code to this adapter.

### Read environment variables inside the adapter

Rejected because multiple instances and tests need deterministic,
caller-controlled configuration without import-time state.

### Retry every error or expose the response message

Rejected because validation/authentication failures are not transient and raw
provider text can contain request details or secrets.

### Silently remove response format on incompatibility

Rejected because it weakens a declared structured-output requirement. The
caller receives a typed capability failure instead.
