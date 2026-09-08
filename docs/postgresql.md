# PostgreSQL + pgvector Adapter

`PostgresMemoryStore` is the durable v0.1 reference adapter. It requires ordinary
PostgreSQL with the public pgvector extension and has no cloud-provider dependency.
From a source checkout, install it separately so the base package remains
dependency-free:

```bash
python -m pip install ".[postgres]"
```

## Configuration and lifecycle

Configuration is explicit; importing the module never reads environment variables
or opens a connection. Supply secrets through the deployment's secret manager and
pass the resolved URL to the constructor:

```python
import os

from portable_memory_engine.adapters.postgres import (
    PostgresMemoryStore,
    PostgresStoreConfig,
    upgrade_postgres_schema,
)

config = PostgresStoreConfig(
    database_url=os.environ["DATABASE_URL"],
    embedding_dimension=1536,
)

await upgrade_postgres_schema(config)
store = PostgresMemoryStore(config)
await store.open()
try:
    ...
finally:
    await store.close()
```

The default `auto_migrate=False` separates application startup from schema
ownership. `upgrade_postgres_schema()` and `downgrade_postgres_schema()` use the
package's new Alembic history. Downgrade removes PME tables but intentionally
leaves the shared `vector` extension installed.

Startup inserts or reads one `pme_store_config` row containing only the configured
embedding dimension. A different dimension is rejected before the store becomes
available. Record embeddings are also checked before writes and query embeddings
before search. An embedding-model change still requires an explicit re-embedding
plan; semantic queries only compare rows with the same model identifier.

The URL field is excluded from configuration repr. The adapter's lifecycle log
uses SQLAlchemy's password-hidden URL rendering, SQL parameter logging is hidden,
and public exceptions contain neither native SQL nor connection details.

## Schema

The initial migration creates only adapter tables and the pgvector extension:

- `pme_memories`: complete tenant/subject/namespace identity, source and session
  provenance, immutable-domain fields, JSON metadata, vector data, and an opaque
  integer version;
- a freshness control table recording the latest accepted timestamp for each
  stored item category;
- `pme_deletion_barriers`: bounded ID, scope, subject, and session barriers;
- `pme_session_contributions`: fact and summary contribution state with tombstones;
- `pme_idempotency`: scoped payload digests and content-free committed results;
- `pme_store_config`: the configured embedding dimension.

There are no users, clients, API keys, prompts, provider settings, operational
parameters, customer mappings, or other business seed rows. Domain `tenant_id=None`
is one concrete partition and is encoded internally as a non-wildcard empty key;
the mapper restores `None` before any value crosses the storage port.

Hard deletion removes selected records, embeddings, and metadata. Freshness,
barrier, and idempotency rows are deliberately retained to prevent late writes and
preserve replay semantics. Operators must define retention for that content-free
control state and for backups separately. The adapter does not claim erasure from
backups, replicas, logs, or external systems.

## Exact search default

The base migration creates no ANN index. Exact pgvector cosine search is the
public default because it is immediately correct on an empty or small deployment,
has no dataset-size training parameter, preserves exact ordering and counts, and
works with the configurable unbounded-vector column after startup validation.

HNSW or IVFFlat can be introduced by a deployment-specific migration after
measuring its data size, latency, recall target, write cost, and supported vector
dimension. Such a deployment must document changed recall/count behavior. ScaNN
is provider-specific and is neither detected nor installed by the base migration.

## Transactions and connection pooling

Every mutation uses a database transaction. A deterministic transaction-level
advisory lock serializes mutations for one tenant-and-subject boundary across
processes; database primary keys and version predicates remain the final unique
and CAS constraints. Atomic `put_many()` acquires all subject locks in stable
order before applying any command, so a later conflict rolls back the whole group.

Neutral defaults are a pool size of 5, overflow of 5, a 30-second checkout
timeout, pre-ping enabled, FIFO SQLAlchemy pooling, and no provider-specific
socket, keepalive, codec-cache, or recycle tuning. Deployments should tune these
values from their own workload and database limits.

## Public pgvector container and integration tests

The repository includes `compose.postgres.yml`, based on
`pgvector/pgvector:pg16`. Set a local test-only password without committing it:

```bash
read -rs PME_POSTGRES_PASSWORD
export PME_POSTGRES_PASSWORD
docker compose -f compose.postgres.yml up -d --wait
```

Then construct the disposable test URL in your shell and run the opt-in suite:

```bash
export PME_TEST_DATABASE_URL="postgresql+psycopg://portable_memory:${PME_POSTGRES_PASSWORD}@127.0.0.1:55432/portable_memory"
pytest tests/integration/test_postgres_store.py
```

The test database must be disposable. The suite upgrades, downgrades, truncates,
and finally removes PME tables. It runs the full shared adapter contract plus
concurrent unique-create, CAS, transaction rollback, migration round-trip, and
dimension-mismatch cases.
