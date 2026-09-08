# Portable Memory Engine v0.1 Product Scope

Status: accepted design input for the v0.1 candidate implementation.

## Purpose

Portable Memory Engine is an async-first Python SDK for extracting durable memories from conversations, storing them behind replaceable adapters, and recalling them by recency or semantic relevance.

The SDK is a library, not a hosted memory service. Applications remain responsible for authentication, authorization at their external boundary, provider selection, deployment, and data-governance decisions.

## Intended users

- Python applications that need durable conversational memory without coupling their domain logic to one database or model provider.
- Adapter authors who need an executable contract for storage, chat-model, embedding, prompt, and policy integrations.
- Teams that want an in-process engine first and may add an HTTP, queue, or agent-tool boundary later.

## v0.1 goals

### Domain and application API

- Framework-independent domain objects with no database, web-framework, telemetry, or provider dependency.
- An async-first `MemoryEngine` that supports add, recall, and delete use cases.
- Structured success, partial-success, conflict, stale-event, validation, capability, and provider-parse results.
- Explicit ownership and async cleanup of adapter resources.

### Default memory behavior

- `summary`: a compact account of one conversation session.
- `fact`: a multi-valued durable statement extracted from user-authored input.
- `profile`: a consolidated view of durable preferences or attributes within one memory scope.
- Replaceable extraction and update strategies; the default kinds are not a closed enum for all applications.

### Storage and providers

- A concurrent in-memory adapter for local use and contract tests.
- A PostgreSQL plus pgvector reference adapter.
- An OpenAI-compatible chat-model adapter configured entirely by the caller.
- A deterministic embedder for tests and at least one provider embedder installed as an optional dependency.
- A fully replaceable prompt provider with original, generic defaults.

### Reliability contracts

- Scope isolation on every read, write, search, and delete operation.
- Idempotent command replay.
- Optimistic concurrency control through opaque versions and compare-and-swap.
- Event-time freshness checks before model work and immediately before persistence.
- Explicit adapter capability checks with no silent semantic downgrade.
- Structured partial results when independent memory kinds do not all succeed.

### Recall and deletion

- Recency and semantic recall with kind and session filters.
- Stable pagination semantics and an explicit distinction between known and unknown total counts.
- Idempotent deletion by memory ID within a complete scope.
- Idempotent deletion by memory ID, complete scope, tenant-and-subject boundary,
  or session contribution with explicit event-time barriers.

### Optional reference integration

- A non-production FastAPI reference app that wraps only the stable public
  `MemoryEngine` facade.
- An independent `fastapi` extra; the base SDK remains usable without FastAPI,
  Pydantic, or an ASGI server.
- Health, add, recall, and delete only, with transport schemas kept separate
  from domain models and restrictive localhost defaults.
- A non-production, stdio-only MCP reference integration exposing add, recall,
  and delete tools over the stable public facade.
- An independent `mcp` extra, explicit complete boundary on every call, an
  injectable authorization hook, and isolated per-connection context.

## Public API boundary

The v0.1 API is asynchronous. A call such as `await engine.add(...)` completes its configured in-process extraction and persistence work before returning; queue delivery is outside the core boundary.

Reads, writes, ID deletes, scope deletes, and session deletes require a
`MemoryScope` containing:

- an optional `tenant_id`;
- a required `subject_id`;
- a required `namespace`.

`source` and `session_id` are provenance and correlation fields. They never replace tenant, subject, or namespace isolation.

Subject-wide deletion is the only base cross-namespace operation. It uses an
explicit `MemorySubject` containing tenant and subject, requires separate
policy/store capabilities, and never crosses tenants.

The engine is an async context manager. By default, it owns and closes lifecycle-capable dependencies passed to it. Advanced callers may explicitly opt out when sharing adapter instances; ownership is never inferred.

## v0.1 non-goals

- A production HTTP or MCP service, network MCP transport, or synchronous wrapper.
- Production authentication, API-key management, rate limiting, or an admin API;
  the optional FastAPI app is reference-only.
- Queue transports, distributed workers, exactly-once delivery, or a workflow framework.
- Redis, cloud deployment templates, or provider-specific infrastructure.
- Compatibility with an unrelated pre-existing database schema.
- Support for every database behind a lowest-common-denominator abstraction.
- Automatic subtraction or reconstruction of shared aggregate text after a
  session contribution is deleted.
- Restoring a deleted session without a strictly newer explicit write.
- Automated regulatory compliance claims.
- Production performance guarantees.

## Security and privacy defaults

- The engine does not log message text, memory content, prompts, model responses, queries, embeddings, or metadata by default.
- Observer events receive operation names, duration, counts, result state, and bounded low-cardinality attributes only.
- Model output is untrusted input and must pass schema, enum, size, and identifier validation.
- Provider credentials and database credentials must be redacted from representations, exceptions, logs, and telemetry.
- Tenant and namespace filters must be executed by the storage adapter, never as post-query in-memory filtering.
- Examples, tests, and benchmarks use synthetic data.

## Success criteria

The v0.1 candidate is complete only when:

1. the base package installs without database, web-framework, cloud, or provider SDKs;
2. the in-memory and PostgreSQL adapters pass the same storage contract suite;
3. idempotency, concurrency, event time, scope isolation, and deletion behavior have explicit tests;
4. a no-network quickstart works with deterministic providers;
5. optional provider dependencies install independently;
6. the optional FastAPI extra installs independently while the base wheel remains
   free of web-framework dependencies;
7. the optional MCP extra installs independently while the base wheel remains
   free of MCP dependencies;
8. package artifacts and the complete new Git history pass security and provenance review.

## Deferred decisions

Project licensing and publication channels must be selected before any public release. They do not change the technical v0.1 architecture described here.
