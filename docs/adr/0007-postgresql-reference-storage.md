# ADR 0007: PostgreSQL Reference Storage

## Status

Accepted for the v0.1 candidate.

## Decision

The first durable reference adapter uses ordinary PostgreSQL, pgvector,
SQLAlchemy's async psycopg dialect, and an independent Alembic history. The base
package remains dependency-free; database dependencies live in the `postgres`
extra.

ORM rows and domain values have an explicit mapper boundary. The schema carries
complete tenant/subject/namespace identity and source/session provenance. Durable
control tables implement scoped idempotency results, CAS versions, event-time
freshness, typed deletion barriers, and session contribution tombstones in the
same transaction as memory mutations.

Embedding dimension is constructor configuration persisted and revalidated at
startup. The vector column has no fixed typmod so one public migration supports
different deployments, while a check constraint and adapter validation prevent
mixed dimensions in a configured store.

Exact cosine search is the public default. HNSW and IVFFlat require an explicit
deployment migration and workload-specific decision. ScaNN is outside the base
migration and may only appear as a future provider-specific optional capability.

Mutations acquire a transaction-level advisory lock for their tenant-and-subject
boundary. This conservative hierarchy makes subject deletion and late-arriving
writes serializable across processes; database uniqueness and version predicates
remain authoritative. Pool defaults are small and neutral, and URL credentials
are hidden in repr, logs, and public errors.

## Consequences

- A public pgvector image is sufficient for development and contract testing.
- The adapter does not depend on AlloyDB, GCP, undocumented asyncpg APIs, or a
  provider-specific index.
- Exact search has predictable correctness but may require an explicit ANN design
  for large deployments.
- The no-tenant partition needs an adapter-private non-wildcard storage key because
  PostgreSQL primary-key columns are non-null.
- Content-free freshness, barrier, and idempotency state survives hard deletion;
  operators own retention and backup policies.
- Schema migration remains an explicit operational action by default.
