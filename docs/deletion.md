# Deletion application service

`portable_memory_engine.application.DeleteEngine` is the async,
framework-neutral deletion service. It accepts typed domain commands and
returns a domain `DeleteResult`; integrations map that result to their own wire
format without encoding behavior in HTTP status codes.

## Commands and authorization

| Command | Store boundary | Physical behavior |
|---|---|---|
| `DeleteMemoryCommand` | Complete scope plus memory ID | Hard delete |
| `DeleteScopeCommand` | Complete scope, optionally filtered by kind | Hard delete |
| `DeleteSubjectCommand` | Exact tenant and subject across namespaces, optionally filtered by kind | Hard delete |
| `DeleteSessionCommand` | Complete scope plus session ID | Per-kind contribution policy |

The injected `AccessPolicy` authorizes `DELETE` before the store receives the
command. ID, scope, and session commands authorize a `MemoryScope`.
Subject-wide deletion authorizes a `MemorySubject`; `tenant_id=None` remains a
concrete single-tenant partition and never means every tenant.

The store performs every filter. The application service never fetches broad
records and deletes or filters them in memory.

## Session contribution policy

- Summary and fact writes with a session ID create adapter-private contribution
  state. Session deletion tombstones those contributions while retaining the
  physical record for controlled regeneration.
- A fact with any tombstoned contribution is hidden in `get`, recency recall,
  and semantic recall. Another contributor cannot reactivate the removed
  contribution.
- A profile is a current rebuildable snapshot. Session deletion hard-deletes it
  only when the current snapshot provenance names that session.
- A custom-kind record whose current provenance names the session is hard
  deleted.

The base package does not attempt to subtract one session's information from
shared text. It uses conservative hiding until an explicit newer regeneration.

## Event time, replay, and regeneration

All commands carry an aware-UTC deletion event timestamp. The store atomically
advances a content-free ID, scope, subject, or session barrier. A write at or
before that barrier raises `StaleEventError`, including late work that creates a
new memory ID.

A write strictly newer than the barrier is intentional regeneration or session
reuse. Rewriting the same summary/fact contribution reactivates it; a newer
profile write builds a new snapshot.

The same idempotency key and digest returns the original result with
`replayed=True`. Reusing the key with another digest raises
`IdempotencyConflictError`. A new command against already-deleted state returns
zero deleted counts without treating absence as an HTTP error.

## Structured counts

`DeleteResult` exposes:

- `matched_count`;
- `hard_deleted_count`;
- `contribution_deleted_count`;
- `tombstoned_count`;
- computed `deleted_count`;
- `already_absent_count` for an ID target;
- the effective `barrier_timestamp` and `replayed` flag.

The count fields do not contain identifiers or content.

## Capabilities and lifecycle

All deletion requires atomic idempotency and deletion barriers. Subject-wide
deletion additionally requires `subject_deletion`; session deletion requires
`session_contributions`. Missing capabilities fail explicitly without a weaker
fallback.

Use `async with engine` or call `open()` and `close()`. The engine owns its
store and observer by default; set `owns_resources=False` for caller-managed
resources. Observation events contain only target category, outcome, duration,
optional single kind, and deleted count. They do not contain scope, subject,
session, memory ID, content, command keys, or policy identity.

## Privacy boundary

Hard deletion removes selected active content, embeddings, and user metadata
from the configured store. Adapters may retain bounded content-free barriers,
contribution references, timestamps, hashes, and idempotency state to prevent
resurrection and replay conflicts.

This contract does not promise deletion from backups, provider logs, caller
telemetry, caches outside the adapter, replicas, exported data, or other
systems. It is not a GDPR or comprehensive regulatory-compliance claim.
