# Adapter Contracts

Portable Memory Engine exposes external dependencies through
`portable_memory_engine.ports`. Ports depend only on the standard library and
the public domain layer. An adapter must not expose database sessions, ORM
objects, SQL operators, HTTP models, provider request objects, credentials, or
native exceptions through these interfaces.

## Resource lifecycle

Resource-owning adapters implement `AsyncLifecycle`:

- a new adapter is unopened;
- `open()` and `close()` are idempotent;
- operations outside the open interval raise the sanitized `LifecycleError`;
- constructing or importing an adapter performs no connection, environment
  lookup, or network operation;
- the caller owns an injected adapter unless a future facade explicitly accepts
  lifecycle ownership.

`Clock`, `IdGenerator`, and `AccessPolicy` do not require lifecycle methods
because their contracts do not imply owned I/O resources.

## Storage capabilities

`StorageCapabilities` describes configured semantics, not current health. The
contract version is independent from the package version. The full v0.1 write
path requires atomic compare-and-swap, atomic idempotency, freshness watermarks,
and deletion barriers.

`MemoryStore.health()` reports current sanitized availability and never changes
the capability declaration.

Optional operations call `StorageCapabilities.require(...)` before expensive or
sensitive work. Missing vector search, transactions, metadata filtering, exact
counts, subject deletion, or session contributions must never be replaced with
a weaker behavior without an explicit caller decision.

## Exception boundary

Adapters translate native failures at their outermost public method:

| Condition | Public result |
|---|---|
| Unsupported declared operation | `CapabilityError` |
| Expected-version mismatch | `ConflictError` |
| Reused key with another digest | `IdempotencyConflictError` |
| Event at or before a competing watermark/barrier | `StaleEventError` |
| Temporary store outage | `StoreUnavailableError` |
| Other sanitized store failure | `StoreError` |
| Temporary model or embedding outage | `ProviderUnavailableError` |
| Other sanitized provider failure | `ProviderError` |
| Provider output schema mismatch | `ProviderParseError` |
| Use before open or after close | `LifecycleError` |

Public exception messages must not contain memory content, prompts, credentials,
connection strings, SQL, native provider payloads, or complete scope values.
The original exception may be retained internally for debugging, but it is not
part of the public contract.

## Reusing the store contract suite

Install the `contract-tests` extra, subclass the suite, and inject a fresh,
unopened adapter through a function-scoped fixture:

```python
import pytest

from portable_memory_engine.testing.contracts import MemoryStoreContractSuite


@pytest.fixture
def memory_store_factory():
    return build_store


class TestMyStore(MemoryStoreContractSuite):
    pass
```

The suite owns each test instance and covers lifecycle, mandatory capabilities,
complete-scope isolation, idempotency, OCC, event-time freshness, pagination,
filtering, deletion barriers, subject isolation, session contribution behavior,
semantic ordering, and truthful optional capabilities. Adapter-specific tests
remain responsible for configuration, native schema/migrations, control-data
retention, health behavior, cleanup, and performance.

## Dependency-free reference adapters

`InMemoryMemoryStore` implements vector search, CAS, idempotency, freshness,
deletion barriers, subject deletion, session contributions, transactions, and
exact counts. It explicitly reports metadata filtering as unsupported. Its
process-local control state is not durable and is not shared across workers.

`DeterministicEmbedder`, `ScriptedChatModel`, `FixedClock`, and
`DeterministicIdGenerator` provide stable, network-free collaborators for tests
and examples. Their outputs are synthetic and make no semantic-quality claim.

## Durable PostgreSQL reference adapter

`PostgresMemoryStore` implements the same declared capabilities through the
optional `postgres` extra. Its tests subclass the same `MemoryStoreContractSuite`
used by `InMemoryMemoryStore`, then add empty-database migration, concurrent
unique-create, concurrent CAS, rollback, and configuration checks.

The adapter uses SQLAlchemy's public async psycopg dialect and pgvector's public
async registration hook. It does not call asyncpg internals. Pool size,
overflow, timeout, and lifecycle are explicit neutral configuration; no cloud
or traffic-derived tuning is embedded. Database URLs are stored out of repr,
rendered with hidden passwords for the single lifecycle log, and never included
in public exceptions.

See [the PostgreSQL guide](postgresql.md) for its schema and operational boundary.

## Google Vertex AI embedding reference adapter

`GoogleVertexEmbedder` implements `Embedder` through the independent
`google-vertex` extra. Project, region, model, dimension, batch size, truncation
policy, API version, and optional credentials are explicit caller inputs. The
base package and core layers do not import the provider SDK.

Provider-neutral document and query purposes become
`RETRIEVAL_DOCUMENT` and `RETRIEVAL_QUERY` only at this adapter boundary. The
adapter copies every native vector into the immutable domain embedding and
validates result count, finiteness, and configured dimension before returning.
An injected async client remains caller-owned; a client constructed by the
adapter is closed only after active calls finish. Cancellation propagates.

See [the Google Vertex AI embedding guide](google-vertex-embeddings.md),
including its model/dimension migration boundary.

## OpenAI-compatible chat reference adapter

`OpenAICompatibleChatModel` implements `ChatModel` through the independent
`openai-compatible` extra. API root, API key, model, timeout, retry policy,
response-size bound, and structured-output mode come from one explicit config;
the adapter reads no environment or module-global settings.

The adapter maps only conversation role/content and provider-neutral token
usage. It detects strict JSON Schema support lazily, may fall back to JSON mode
only after an explicit response-format rejection, and raises `CapabilityError`
instead of silently removing a requested schema. Transport failures, 429, and
5xx use bounded retries with `Retry-After`; cancellation propagates. Public
errors never include the key, request payload, response body, or native HTTP
exception text. See [the adapter guide](openai-compatible.md).
