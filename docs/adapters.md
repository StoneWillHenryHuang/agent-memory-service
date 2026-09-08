# Adapter and Integration Matrix

Portable Memory Engine keeps external systems behind optional adapters. Install
only the implementation selected by the application; none is selected from an
environment variable or global registry.

## Installation and intended use

| Component | Import surface | Install | Network | Intended use |
|---|---|---|---|---|
| In-memory store | Package root | Base package | No | Tests, examples, single-process development |
| Scripted chat model | Package root | Base package | No | Deterministic tests and examples |
| Deterministic embedder | Package root | Base package | No | Deterministic tests and examples |
| Default prompt provider | Package root | Base package | No | Generic default prompt contracts |
| PostgreSQL + pgvector store | `portable_memory_engine.adapters.postgres` | `.[postgres]` | Database connection | Durable reference storage |
| OpenAI-compatible chat | `portable_memory_engine.adapters.openai_compatible` | `.[openai-compatible]` | Caller URL | Generic Chat Completions reference |
| Google Vertex embedder | `portable_memory_engine.adapters.google_vertex` | `.[google-vertex]` | Caller project/region | Provider embedding reference |
| FastAPI wrapper | `portable_memory_engine.integrations.fastapi` | `.[fastapi]` | Loopback reference server | Non-production HTTP example |
| MCP wrapper | `portable_memory_engine.integrations.mcp` | `.[mcp]` | Process stdio only | Non-production tool example |

The dot in an install target means a source checkout. The project is not yet a
published package. Provider and integration extras are independent: selecting
one does not intentionally install another.

## Storage capability matrix

Both included stores implement storage contract `1.1` for the capabilities they
declare.

| Capability | In-memory | PostgreSQL + pgvector |
|---|---:|---:|
| Vector search | Yes | Yes, exact cosine search |
| Atomic compare-and-swap | Yes | Yes |
| Atomic idempotency | Yes | Yes |
| Freshness watermarks | Yes | Yes |
| Deletion barriers | Yes | Yes |
| Arbitrary metadata filtering | No | No |
| Atomic write groups | Yes | Yes |
| Exact total count | Yes | Yes |
| Subject deletion | Yes | Yes |
| Session contributions | Yes | Yes |
| Durable across process restart | No | Yes, subject to database operations |
| Multi-process coordination | No | Yes, through PostgreSQL transactions and locks |

Kind, source, and session filters are part of the public query contract even
though the broader arbitrary-metadata capability is false. Capability values
describe configured semantics, not current health; adapters report health
separately.

The in-memory store is concurrency-safe within one process but is not a cache,
database, backup, or durability mechanism. The PostgreSQL adapter uses ordinary
PostgreSQL with pgvector and exact search by default. Approximate indexing and
provider-specific database features are not configured by the package.

## Model and embedding behavior

| Behavior | Scripted chat | OpenAI-compatible chat | Deterministic embedder | Google Vertex embedder |
|---|---:|---:|---:|---:|
| Base dependency | Yes | No | Yes | No |
| Caller selects model | Script-owned | Yes | Fixed deterministic algorithm | Yes |
| Caller selects endpoint/location | Not applicable | Yes | Not applicable | Yes |
| Structured output | Script-owned response | Detects configured endpoint support | Not applicable | Not applicable |
| Retry policy | No network | Bounded transport/429/5xx retry | No network | Provider client behavior plus local validation |
| Output validation | Strict engine parser | Size and strict engine parser | Count/dimension/finite checks | Count/dimension/finite checks |
| Production quality claim | No | No | No | No |

The OpenAI-compatible adapter targets a public request shape, not every server
that describes itself as compatible. Endpoint behavior must be tested by the
deployer. The Google Vertex adapter validates the configured dimension but does
not migrate an existing vector index when the model or dimension changes.

## Choosing components

- Start with the base adapters for local integration and deterministic tests.
- Select PostgreSQL when durable, multi-process coordination is required and
  the deployment can own migrations, backups, encryption, monitoring, and
  capacity planning.
- Select provider adapters only after deciding endpoint trust, credentials,
  retention, quotas, regional controls, and failure budgets.
- Treat the FastAPI and MCP packages as mapping examples. They are not
  deployment templates or authentication systems.
- Use a custom adapter when required behavior cannot be represented honestly by
  an included adapter. Do not claim a capability that is emulated with weaker
  semantics.

## Custom adapter acceptance

A storage adapter should:

1. implement `MemoryStore` without leaking native sessions or rows;
2. enforce complete scope inside every query and mutation;
3. declare only end-to-end atomic capabilities it actually provides;
4. translate native failures to sanitized public exceptions;
5. implement idempotency, freshness, CAS, and deletion barriers atomically;
6. pass the reusable contract tests for every declared capability;
7. document lifecycle ownership, pagination, count precision, vector scoring,
   migration, retention, and deletion limits.

A model or embedding adapter should validate response size and shape, preserve
cancellation, bound retries, sanitize errors, and keep credentials out of
representations and logs.

See [adapter contracts](adapter-contracts.md), [compatibility](compatibility.md),
and the individual [PostgreSQL](postgresql.md),
[OpenAI-compatible](openai-compatible.md), and
[Google Vertex](google-vertex-embeddings.md) guides.
