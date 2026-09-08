# ADR 0004: Idempotency, Compare-and-Swap, and Event-Time Freshness

- Status: Accepted
- Date: 2026-07-30

## Context

Model calls are slow relative to local state changes. The same command may be retried, two writers may update the same logical memory, and an older source event may arrive after a newer one. Last-write-wins by processing time would make results depend on scheduling rather than source chronology.

## Decision

v0.1 treats idempotency, optimistic concurrency, and freshness as separate mandatory contracts.

### Idempotency

Each mutating command includes:

- a complete scope;
- an `idempotency_key`;
- a canonical payload digest;
- an event timestamp when the command is event-derived.

The store atomically claims or observes `(scope, idempotency_key)`:

- a new key is claimed for one execution;
- the same key and digest returns the previously committed structured result;
- the same key with another digest raises an idempotency conflict;
- a failed transient attempt may be retried according to a documented claim state and lease/cleanup policy;
- a permanent validation or parse failure is represented explicitly and is not converted to success.

Idempotency records contain only the minimum identifiers, digests, state, and sanitized result metadata required for replay. They do not retain full messages or model payloads.

### Optimistic concurrency

Store versions are opaque. Conditional writes use an expected version:

- `None` means create only if absent;
- an exact match permits the mutation and issues a new version;
- absence or mismatch returns a conflict.

The application may retry a conflict a bounded, configurable number of times. Every retry re-reads the target, re-evaluates the update, and repeats freshness checks. Exhaustion returns a structured conflict; it never becomes an unconditional write.

### Event-time freshness

Every event-derived mutation has a timezone-aware UTC `event_timestamp`. Freshness watermarks are stored for the logical target under the complete scope.

The engine checks the watermark:

1. before a model call, to avoid unnecessary work for an already stale command;
2. immediately before persistence, to detect a newer commit during model work;
3. atomically with the conditional write, so the check and update cannot race inside the adapter.

An event older than the accepted watermark raises a stale-event result. Equal timestamps are accepted only for replay of the same idempotency key and payload. Competing commands at the same timestamp conflict unless their strategy publishes a deterministic tie-breaker.

Processing timestamps from the injected `Clock` populate `created_at` and `updated_at`; they never replace event time for freshness.

### Deletion barriers

ID and scope deletions record a content-free event-time barrier. Events at or before that barrier cannot recreate deleted content. A later event may create new content unless a caller chooses a stricter external retention policy.

### Partial results

Independent memory kinds may commit independently. The engine returns an outcome for each requested kind plus an overall status. Atomic all-kind behavior is available only when explicitly requested and supported by a transactional adapter capability.

## Consequences

- Command replay is stable under at-least-once invocation.
- Older source events cannot overwrite newer accepted state, even when model calls finish out of order.
- Adapters must implement atomic idempotency claims, CAS, watermarks, and deletion barriers for the v0.1 engine's full write feature set.
- Minimal control metadata may remain after content deletion to prevent stale recreation; retention and physical cleanup must be documented by each adapter.
- Some operations may return partial success rather than pretending to be globally transactional.

## Rejected alternatives

### Last writer by processing time wins

Rejected because network and model latency would determine user-visible truth.

### Check freshness only before the model call

Rejected because a newer event can commit while the model is running.

### Retry conflicts with an unconditional overwrite

Rejected because it defeats OCC and can restore stale data.

### Use a transport message ID as the idempotency key

Rejected because transport retries and domain retries have different lifecycles and envelopes.
