# v0.1 Compatibility and Limitations

This document states what the Portable Memory Engine v0.1 candidate intends to support. It is a contract target, not a claim that unfinished features already work.

## Runtime support

| Area | v0.1 target |
|---|---|
| Python | CPython 3.12 and later versions included in the tested matrix |
| Public API | Async only |
| Base installation | Domain, ports, application services, and in-memory/test adapters without provider or database SDKs |
| Configuration | Constructor arguments and neutral configuration objects; no import-time environment loading |
| Time | Timezone-aware UTC values only |
| Stable facade | Root `MemoryEngine` composes add, recall, and delete with explicit injected dependencies |
| Optional HTTP reference | FastAPI extra; localhost-only, non-production add/recall/delete/health wrapper |
| Optional MCP reference | MCP extra; stdio-only, non-production add/recall/delete tools |

## Memory behavior

| Capability | v0.1 status | Notes |
|---|---|---|
| Summary | Implemented | One default logical summary per scope/session/strategy version |
| Fact | Implemented | Multi-valued with add, update, delete, and no-op actions |
| Profile | Implemented | One default logical profile per scope/profile name/strategy version |
| Custom kinds | Planned extension point | Default extractors cover summary, fact, and profile only |
| Idempotent replay | Required | Same scoped key and payload returns the original result |
| OCC/CAS | Required | No unconditional overwrite fallback |
| Event-time freshness | Required | Checked before model work and before persistence |
| Partial success | Implemented | Structured per-kind outcomes |

## Recall

| Mode | v0.1 status | Required store capability |
|---|---|---|
| Recency | Implemented | Ordered query and scope filtering |
| Semantic | Implemented | Vector search and compatible embedding dimension |
| Kind filter | Implemented | Native store-side filtering |
| Session/source filter | Implemented | Native store-side filtering |
| Pagination | Implemented | Stable ordering; total count may be exact, approximate, or unavailable |
| Reranking | Extension point | Async no-op default; no reranking provider is included in the base package |

Semantic recall does not fall back to recency when vector search is unavailable.

## Storage adapters

| Adapter | v0.1 target | Notes |
|---|---|---|
| In-memory | Reference | Concurrent, deterministic, and contract-complete for declared capabilities |
| PostgreSQL + pgvector | Implemented reference | Optional `postgres` extra; exact cosine search and independent migrations |
| Provider-specific PostgreSQL optimizations | Not part of the base adapter | May be added later as optional capabilities |
| Other databases | Not supported by default | Community adapters must pass the shared contract for declared capabilities |

The SDK does not read, migrate, or promise compatibility with an unrelated
pre-existing schema. The adapter starts with a new migration history.
Its base migration has no provider-specific extension, ANN index, or business
seed. The adapter stores a single non-business embedding-dimension setting when
it first opens and rejects a mismatched later configuration.

## Model and embedding adapters

| Adapter | v0.1 target | Installation |
|---|---|---|
| Scripted chat model | Test/reference | Base or development surface, no network |
| OpenAI-compatible chat model | Implemented reference | Independent `openai-compatible` extra; caller-configured Chat Completions HTTP |
| Deterministic embedder | Test/reference | No network |
| Google Vertex AI embedder | Implemented reference | Independent `google-vertex` extra; caller-configured project, region, model, dimension, and batching |

Base installation must not pull cloud SDKs or make network requests. Provider models, URLs, regions, credentials, dimensions, timeouts, and retry policies are caller configuration.

The package-root facade imports no optional provider or database SDK. Its exact
exports and copyable README quickstart are tested. The facade does not imply
support for a backend or provider beyond the adapters listed in this document.

The OpenAI-compatible adapter retries transport failures, 429, and 5xx only,
honors bounded `Retry-After`, and detects `json_schema`/`json_object` response
format support without silently dropping a requested schema. It has no built-in
endpoint, model, API key source, provider-specific header, or gateway
assumption.

The Google Vertex AI adapter maps provider-neutral document/query tasks to
provider task hints inside the adapter, requests the configured output
dimension, and rejects empty, non-finite, count-mismatched, or
dimension-mismatched output before persistence. Its tests use no cloud account.

## Deletion

Supported v0.1 targets:

- a memory ID plus complete scope;
- all records in a complete tenant/subject/namespace scope;
- one tenant-and-subject boundary across namespaces;
- one session's contributions inside a complete scope;
- an optional kind restriction for scope or subject deletion.

Implemented behavior:

- ID/scope/subject targets hard-delete selected records and advance event-time barriers;
- session deletion tombstones summary/fact contributions and hard-deletes the current session-owned profile/custom snapshot;
- events at or before a deletion barrier cannot restore content;
- strictly newer writes are explicit regeneration or session reuse;
- repeated commands are idempotent and return structured counts.

Explicitly unsupported in v0.1:

- automatic subtraction or reconstruction of shared aggregate text after contribution deletion;
- a restore operation for deleted sessions;
- a claim of erasure from provider logs, backups, replicas, or external systems.

## Optional HTTP reference integration

The `fastapi` extra provides an implemented, non-production reference app over
the stable package-root facade. It has independent Pydantic transport schemas,
an explicit mapper, generated OpenAPI, a server-generated request ID, an
injectable authorization hook, an empty CORS allowlist, and explicit localhost
trusted hosts. The default hook accepts only loopback IP peers.

The app intentionally has no admin surface, client registry, Redis dependency,
rate limiter, custom header convention, environment loader, or cloud manifest.
It disables HTTP documentation routes so the registered route set remains
exactly health, add, recall, and delete; callers can export `app.openapi()`.

## Optional MCP reference integration

The `mcp` extra provides an implemented, non-production tools-only server over
the stable package-root facade. It advertises exactly add, recall, and delete,
uses explicit project-owned JSON Schemas, generates one opaque context per
connection, and invokes an authorization hook for every tool call after strict
input validation. It does not use client identity or source conventions.

The supported reference transport is process stdio only. Streamable HTTP, SSE,
OAuth, bearer or API-key handling, resources, prompts, sampling, roots, tasks,
logging notifications, and production hosting are not included. The integration
removes the SDK's default telemetry middleware and emits no content logs.

## Integrations not included in v0.1

- a production FastAPI or other hosted HTTP service;
- a production or network MCP service;
- synchronous facade;
- Redis caches or rate limits;
- queue consumers, outbox/inbox, dead-letter queues, or exactly-once delivery;
- workflow, scheduler, or distributed execution frameworks;
- cloud deployment manifests and production authentication.

These exclusions are deliberate. Optional integrations must depend on the public
facade and cannot become base-package dependencies. The reference FastAPI app
does not change this deployment and security boundary.

## Capability failure behavior

An operation validates its required adapter capabilities before doing work. Unsupported behavior raises a typed capability error. The engine does not silently:

- downgrade semantic recall to recency;
- replace CAS with last-write-wins;
- remove tenant or namespace filtering;
- emulate transactions with undocumented partial writes;
- accept a mismatched embedding dimension.

## Schema and version compatibility

Domain serialization, prompt output, database migrations, event envelopes, and HTTP schemas are separate versioned contracts. v0.x may introduce breaking changes, but each change requires a changelog entry and migration guidance.

Published database migrations are immutable. Embedding-model or dimension changes require an explicit re-index plan. The public import surface will be tested so internal refactors do not accidentally redefine the supported API.

## Security and operational limitations

The SDK is not a security boundary by itself. Deployments must provide authentication, external authorization, encryption, backups, retention, regional controls, and secret management.

Default logs and observers exclude content, but custom adapters and observers remain the deployer's responsibility. Default prompts reduce neither prompt injection nor model-output risk to zero; all model output is validated as untrusted data.

Performance results, when added, will use synthetic data and reproducible public environments. They will not be production guarantees.
