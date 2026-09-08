# ADR 0005: Scoped, Subject, and Session Contribution Deletion

- Status: Accepted
- Date: 2026-07-30
- Revised: 2026-07-31

## Context

A memory derived from one conversation can later contribute to an aggregate
also influenced by other conversations. Deleting a session is therefore not
equivalent to deleting rows whose latest provenance has that session ID.
Deletion needs explicit physical behavior, contribution visibility, replay
rules, late-arrival protection, and isolation boundaries.

The base SDK cannot promise removal from backups, provider systems, caller
telemetry, external replicas, or infrastructure outside the configured store.

## Decision

### Typed deletion commands

The domain exposes four commands:

1. `DeleteMemoryCommand`: one memory ID in one complete `MemoryScope`;
2. `DeleteScopeCommand`: one complete scope, optionally filtered by kind;
3. `DeleteSubjectCommand`: one `MemorySubject` across namespaces, optionally
   filtered by kind, without crossing the tenant boundary;
4. `DeleteSessionCommand`: one session inside one complete scope.

Every command carries an idempotency key, payload digest, and timezone-aware
deletion event timestamp. Results distinguish hard-deleted records, deleted
contributions, tombstoned contributions, already-absent IDs, and the effective
barrier timestamp. HTTP status concepts are not domain outcomes.

Subject-wide deletion is an explicit administrative boundary rather than a
wildcard. It requires both policy approval for `MemorySubject` and the store's
`subject_deletion` capability. Filtering is performed by the store.

### Physical and per-kind behavior

| Target or kind | Behavior |
|---|---|
| ID, scope, or subject | Hard-delete selected content, embeddings, and user metadata; retain only bounded content-free barriers and idempotency state |
| Session + summary | Tombstone the session contribution; keep the physical record for controlled regeneration |
| Session + fact | Tombstone that contributor; if any contribution is tombstoned, hide the whole fact until the removed contribution is regenerated |
| Session + profile | Hard-delete the current profile snapshot only when that snapshot's provenance names the deleted session |
| Session + custom kind | Hard-delete a current record whose provenance names the deleted session |

The SDK does not claim to subtract one conversation from shared summary or fact
text. Hiding a shared fact is conservative and deterministic; reconstruction
requires a newer explicit write.

Contribution references, deletion timestamps, and session termination markers
are adapter-private control data. A sentinel, native soft-delete column, join
table, or another representation must never enter domain records or public
results.

### Late arrival, replay, and regeneration

ID, scope, subject, and session barriers use event time. A write at or before
the applicable barrier is stale and cannot restore deleted content, including a
late extraction that produces a previously unseen memory ID.

A write strictly newer than the barrier is an explicit regeneration or session
reuse:

- a summary or fact contribution for the same session-memory pair becomes
  active again;
- a new fact ID from that session may become visible;
- a profile is rebuilt as a new current snapshot;
- another contributor cannot reactivate a contribution deleted from a shared
  fact.

Replaying the same deletion command returns its original result with
`replayed=True`. A new deletion command against already-deleted state returns
zero deleted counts while preserving or advancing the content-free barrier.

### Capability and isolation behavior

All adapters implement ID and scope deletion barriers. Subject-wide deletion
requires `subject_deletion`; session behavior requires
`session_contributions`. Missing optional capabilities raise `CapabilityError`
without a weaker fallback.

Session deletion always carries a complete scope. Subject deletion carries an
exact tenant-and-subject boundary. Neither can cross tenants, and session
deletion cannot cross namespaces.

## Consequences

- Late events and old replays cannot silently resurrect deleted content.
- Summary/fact contribution deletion is visible and testable without claiming
  text reconstruction.
- Profile deletion is physical only for the current session-owned snapshot.
- Minimal content-free control metadata may survive content deletion.
- Adapter documentation must state retention and cleanup of that control data.
- Documentation must not claim GDPR or comprehensive regulatory erasure.

## Rejected alternatives

### Query IDs and delete them one by one

Rejected because it races with late writes, loses contribution meaning, and
cannot provide one atomic scoped barrier.

### Remove one contributor but keep shared aggregate text visible

Rejected because the remaining text may still contain information derived from
the removed session.

### Delete then allow unrestricted replay

Rejected because old or duplicate events could immediately restore content.

### Claim erasure across providers and backups

Rejected because the SDK cannot verify systems outside the configured adapter
boundary.
