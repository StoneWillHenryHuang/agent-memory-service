# Recall application service

`portable_memory_engine.application.RecallEngine` is the async application
service for scoped memory recall. It returns domain records and pagination
metadata directly; HTTP response models, source descriptions, display labels,
authentication identities, and application settings belong in integrations.

## Request and scope

`RecallRequest` always contains a domain `MemoryQuery`. The query carries the
complete tenant, subject, and namespace scope plus optional memory-kind,
source, session, offset, and limit filters. Policy approval never removes this
scope from the store call. The default selector passes the complete query to
`MemoryStore.query` or copies every filter into `SemanticQuery`; it does not
fetch broadly and filter records in application memory.

An injected `AccessPolicy` authorizes `READ` for the complete scope before
selection. A denial raises `AccessDeniedError` before the store or embedding
provider receives work.

## Selection modes

`RecallMode.RECENCY` delegates ordering and pagination to
`MemoryStore.query`. The public store contract uses the requested time order
and a deterministic memory-ID tie-breaker.

`RecallMode.SEMANTIC` requires non-empty query text. The default selector:

1. checks the store's `vector_search` capability;
2. checks that a query embedder was supplied;
3. creates exactly one query-task embedding;
4. calls `MemoryStore.semantic_search` with the complete scope and filters.

Capability checks happen before embedding. Semantic recall never silently
falls back to recency. The query text is bounded by `RecallLimits` and is kept
out of default representations and observation events.

## Pagination and counts

`RecallResult.page` is a domain `MemoryPage`. It preserves the adapter's
offset, limit, next offset, total count, and `CountPrecision`:

- `EXACT` means the count is exact for the complete store-side filter;
- `APPROXIMATE` means the numeric count is an estimate;
- `UNAVAILABLE` requires `total_count=None`.

An empty result remains a fully formed page. Zero means an exact or approximate
zero only when the corresponding precision says so; it is never a substitute
for an unavailable count.

## Reranking

`RerankStrategy` is an async extension point over one already bounded selected
page. `NoOpReranker` preserves store order and semantic scores. A custom
strategy may reorder or remove selected candidates, but the engine rejects
duplicate identities, changed records, and records that were not in the
selected page. Pagination metadata remains the store's selection metadata.

No provider-specific reranker or implicit network call is included in the base
package.

## Lifecycle and observability

Use `async with engine` or call `open()` and `close()`. The engine owns its
store, optional embedder, and observer by default; set `owns_resources=False`
when the caller manages shared resources.

The built-in observer contract receives only a mode-specific operation name,
outcome, duration, optional single kind, and result count. It cannot receive
query text, scope, source, session, memory content, IDs, embeddings, metadata,
or access-policy identity. The application service does not configure log
handlers or import a telemetry SDK.
