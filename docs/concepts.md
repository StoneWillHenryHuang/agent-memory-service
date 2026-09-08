# Concepts and Behavioral Semantics

This document defines the public vocabulary and normative v0.1 behavior of Portable Memory Engine.

## Memory scope

A `MemoryScope` is the mandatory isolation boundary for every operation.

| Field | Meaning | Rule |
|---|---|---|
| `tenant_id` | Optional tenant boundary | `None` means a deliberately configured single-tenant space, not a wildcard |
| `subject_id` | The person or entity the memory is about | Required, non-empty, and never inferred from a memory ID |
| `namespace` | Application, agent, project, or logical memory space | Required; defaults to the literal `default` only when the caller accepts that boundary |

The storage identity of a record is `(tenant_id, subject_id, namespace, memory_id)`. A memory ID is not globally addressable. The same ID may exist in two scopes without collision, and a caller cannot read or delete a record by ID alone.

## Provenance

Provenance describes where a memory came from without widening its access boundary.

| Field | Meaning |
|---|---|
| `source` | A channel such as `chat`, `voice`, or `api` |
| `session_id` | A conversation or batch correlation identifier |
| `event_timestamp` | When the source event happened |
| `input_digest` | A one-way digest used for replay diagnostics; it is not an encryption or secrecy boundary |
| `model_id` | The caller-configured model identifier, when recording it is enabled |
| `prompt_version` | Version of the prompt contract used for extraction |
| `schema_version` | Version of the parsed model-output schema |

Raw conversation history is not stored as provenance by default. `source` and `session_id` are filters and audit context, not substitutes for scope.

## Memory kinds

### Summary

A summary is session-scoped. The default identity strategy produces one logical summary for `(scope, session_id, strategy_version)`. Creating a newer summary updates that logical record through optimistic concurrency rather than adding an unbounded duplicate.

A session ID is required by the default summary strategy. Applications that need another summary boundary must provide a replacement strategy.

### Fact

Facts are multi-valued. The default strategy derives a fact ID from a versioned canonicalization of its normalized content within a scope. Re-extracting the same canonical fact converges on the same logical record. Updates and deletes target an existing fact ID; semantic similarity alone never authorizes an overwrite.

Canonicalization is versioned because changing it can change identity. A future canonicalization version requires an explicit migration or coexistence policy.

### Profile

The default profile strategy produces one logical profile for `(scope, profile_name, strategy_version)`, where `profile_name` defaults to `default`. It is updated with compare-and-swap and an event-time guard.

Profiles summarize durable attributes; they are not an authorization profile and do not contain credentials.

### Extensibility

The three default kinds are stable public values, but storage and application ports must allow namespaced custom kinds. An adapter must not assume that only three values can ever exist.

## Command identity and record identity

Record identity answers “which memory is this?” Command identity answers “has this request already been applied?” They are separate.

Every add command carries a caller-provided `idempotency_key`. The key is unique within the complete scope. Replaying the same key with the same canonical payload returns the original structured result without applying writes again. Reusing the key with a different canonical payload raises an idempotency conflict.

Transport message IDs are not idempotency keys. A future queue adapter may deliver the same domain command under different transport envelopes.

## Time

All public datetimes are timezone-aware and normalized to UTC at the boundary. Naive datetimes are validation errors.

### Event time

`event_timestamp` is supplied by the source and represents when the source event occurred. It participates in freshness decisions.

### Processing time

`created_at` and `updated_at` come from the injected engine clock and represent persistence time. They never decide whether source data is newer.

### Freshness watermark

Freshness is evaluated for the logical record or strategy target being updated. A command older than the target's accepted event timestamp is stale and cannot overwrite it.

The engine checks freshness before invoking a model and again immediately before persistence. The second check protects against a newer event that commits while model work is in flight.

Equal event timestamps follow these rules:

- the same idempotency key and payload is a replay;
- a different command that would mutate the same logical target is a conflict unless a strategy defines a deterministic tie-breaker in its public contract;
- no adapter may silently use processing order as the tie-breaker.

## Optimistic concurrency control

Every mutable record carries an opaque `version` issued by its store. A conditional write contains the expected version:

- expected `None` means “create only if absent”;
- a matching version permits the mutation and returns a new version;
- a missing or mismatched version produces a structured conflict.

The application may retry a bounded number of times by re-reading, recomputing, and rechecking freshness. It must surface the final conflict and may not fall back to an unconditional overwrite.

## Partial results

Summary, fact, and profile work may be independent. `MemoryEngine.add` returns a result per requested kind plus an overall state. A failure in one kind does not erase successful writes from another kind unless the caller explicitly requests an atomic operation and the adapter declares transaction support for that operation.

HTTP status codes and provider exception strings are not application result models.

## Recall

Recency recall orders by public time fields with a deterministic memory-ID tie-breaker. Semantic recall embeds the query, delegates scope filtering and similarity ordering to a capable store, and returns a bounded page.

Queries may filter by memory kind, source, and session ID, but every query also includes the full scope. A total count is represented as known, approximate, or unavailable; `0` is not used to mean “unknown.”

When vector search is unavailable, semantic recall raises a capability error. It never silently changes to recency recall.

## Embeddings

An embedding is an immutable value containing vector values, provider/model identity, task type, and dimension. Dimension is validated before persistence. Query and document task hints are provider-neutral in the core and translated only by provider adapters.

Changing an embedding model or dimension requires an explicit re-embedding or index-migration plan. A store must not mix incompatible vectors in one index without declaring how they are separated.

## Deletion in v0.1

Deletion is always scoped and idempotent.

- Delete by ID requires the complete scope and affects at most one record in that scope.
- Bulk scope deletion requires tenant, subject, and namespace and deletes all matching kinds unless a narrower kind filter is supplied.
- Subject deletion uses an explicit tenant-and-subject boundary and spans only that subject's namespaces.
- Session deletion remains inside one complete scope: summary/fact contributions are tombstoned, while the current session-owned profile/custom snapshot is hard-deleted.
- Repeating a completed delete returns stable zero/additional-deletion counts without error.

ID, scope, subject, and session deletes retain content-free event-time barriers
so old or late events cannot recreate deleted content. A strictly newer write is
an explicit regeneration or session reuse. The engine does not automatically
subtract a deleted contribution from aggregate text; a shared fact stays hidden
until the removed contribution is regenerated.

The SDK makes no regulatory compliance claim. Applications must validate whether configured adapters, backups, provider retention, and deployment controls satisfy their obligations.

## Adapter capabilities

Stores declare capabilities such as vector search, atomic compare-and-swap, metadata filtering, transactions, and session contributions. The engine validates required capabilities before starting an operation. Missing capabilities produce a typed error and no partial fallback.

## Async lifecycle

The engine is used as an async context manager. It closes lifecycle-capable dependencies it owns. Shared dependencies require an explicit `owns_resources=False` configuration and remain the caller's responsibility.

Cancellation propagates. Adapters may perform bounded cleanup, but they must not convert cancellation into a successful domain result.

## Observability and content safety

The default observer is a no-op. Observer events contain bounded operational metadata only: operation, duration, counts, result category, adapter type, and low-cardinality kind values.

Message text, memory content, query text, prompts, model responses, embeddings, metadata, tokens, and connection strings are excluded by default. Explicit content observability is outside the standard observer contract.

## Mutability, equality, hashing, and serialization

Domain values are frozen dataclasses or immutable value objects. Mutable input sequences and JSON mappings are copied and recursively frozen at construction, so later caller mutation cannot change a record. Equality and hashing use the normalized domain value, including complete scope; they do not imply authorization.

Domain values are not wire schemas. The SDK does not promise that `dataclasses.asdict`, `repr`, pickle, or an object's internal field layout is a stable interchange format. Adapters and integrations must map values explicitly to their separately versioned database, prompt-output, event, or HTTP schemas. Content-bearing fields are excluded from default representations.

Semantic scores exposed by v0.1 domain results are normalized to the inclusive range `0.0..1.0`; provider adapters own any provider-specific score conversion.
