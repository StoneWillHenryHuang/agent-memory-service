# ADR 0006: Explicit Adapter Capabilities and Fail-Fast Validation

- Status: Accepted
- Date: 2026-07-30

## Context

Storage systems differ in vector search, atomic conditional writes, filtering, transactions, and counting. Pretending every backend supports the same behavior encourages silent fallbacks that weaken scope isolation or reliability.

The engine needs a neutral way to validate an operation before expensive model work begins.

## Decision

Each storage adapter exposes an immutable `StorageCapabilities` value and a contract version. v0.1 capability fields include:

| Capability | Meaning |
|---|---|
| `vector_search` | Native or adapter-coordinated similarity search with store-side scope filtering |
| `atomic_compare_and_swap` | Expected-version check and write are atomic |
| `atomic_idempotency` | Scoped command claim and committed-result replay are atomic |
| `freshness_watermarks` | Event-time comparison and watermark advance can be atomic with writes |
| `deletion_barriers` | Scoped content-free barriers prevent stale recreation after deletion |
| `metadata_filtering` | Declared public metadata predicates run inside the adapter |
| `transactions` | The adapter can atomically group the operations described by its transaction contract |
| `exact_total_count` | Query pages can provide an exact count without changing filter semantics |
| `subject_deletion` | The adapter can atomically delete and barrier one tenant-and-subject boundary across namespaces |
| `session_contributions` | The adapter can represent contribution tombstones, session barriers, and per-kind deletion behavior |

### Validation timing

The facade validates capabilities required by configured engine features during construction. Operations with optional behavior validate any additional capability before embedding, model calls, or writes.

Examples:

- the full v0.1 add path requires atomic idempotency, CAS, freshness watermarks, and deletion barriers;
- semantic recall requires vector search;
- metadata predicates require metadata filtering;
- atomic all-kind add requires transactions;
- subject-wide deletion requires subject deletion;
- session contribution deletion requires session contributions.

### Failure behavior

A missing capability raises a typed `CapabilityError` naming the neutral capability and operation. The engine does not silently:

- substitute recency for semantic recall;
- use last-write-wins instead of CAS;
- perform scope filtering after retrieval;
- ignore a metadata predicate;
- emulate an atomic group with undocumented partial writes;
- accept an incompatible vector dimension.

An application may explicitly select a different supported operation after receiving the error. That is a caller decision, not an engine fallback.

### Split storage systems

An adapter that stores documents and vectors in separate systems is still one adapter at the port boundary. It must document its consistency model and own compensation, reconstruction, or outbox behavior. It may report a capability as true only when its end-to-end behavior satisfies the shared contract.

### Health and capability are distinct

Capabilities describe supported semantics. Health describes current availability. A temporarily unhealthy vector index does not change `vector_search` to false; it produces an operational error.

## Consequences

- Unsupported behavior fails before costly or sensitive provider calls.
- Adapter contracts can test only declared optional features while still enforcing mandatory v0.1 write semantics.
- Capability additions can be versioned independently from package versions.
- Adapter authors must document consistency, count accuracy, filtering, and transaction boundaries.

## Rejected alternatives

### Lowest-common-denominator store interface

Rejected because it would remove vector search, OCC, or deletion safety from the useful core contract.

### Detect support only when a method fails

Rejected because failures would occur after partial work and could expose data to providers unnecessarily.

### Infer capabilities from adapter class or package version

Rejected because forks, configuration, and deployed database features can differ from package metadata.
