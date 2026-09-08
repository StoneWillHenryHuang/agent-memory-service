# ADR 0001: Layered, Dependency-Inverted Architecture

- Status: Accepted
- Date: 2026-07-30

## Context

A memory engine coordinates domain rules, model calls, embeddings, persistence, policy, and observability. If those concerns share framework or provider types, the base package becomes difficult to test and every adapter change leaks into the public API.

v0.1 must support an in-memory implementation and a PostgreSQL/pgvector implementation without treating either as the domain model. It must also remain usable without a web framework or network provider.

## Decision

The dependency direction is:

```text
domain <- ports <- application <- public facade
             ^          ^
             |          |
          adapters   optional integrations
```

### Domain

`domain` contains immutable or deliberately mutable value objects, commands, queries, results, enums, and domain errors. It depends only on the Python standard library.

It does not import Pydantic, SQLAlchemy, a vector extension, a web framework, telemetry libraries, or provider SDKs.

### Ports

`ports` defines behavior-oriented Python protocols for storage, chat models, embeddings, prompts, access policy, time, identifiers, lifecycle, and observation. Ports depend only on `domain` and the standard library.

Ports never expose database sessions, ORM models, HTTP response types, provider request objects, SQL operators, or cloud configuration.

### Application

`application` implements extraction, update, freshness, recall, and deletion use cases. It depends only on `domain` and `ports`.

Application results are structured domain/application values. They do not contain HTTP status codes or raw provider exception messages.

### Public facade

The package root exports the intentionally supported API. Documentation and examples import from that facade rather than internal modules.

The facade assembles an async-first `MemoryEngine`, validates required capabilities, and makes resource ownership explicit.

### Adapters

Adapters implement ports and may depend on third-party libraries. Each provider or infrastructure family is an optional dependency extra. Core modules never import adapters.

ORM models and database migrations live inside the storage adapter boundary. Provider task names, retry behavior, and client objects live inside provider adapters.

### Optional integrations

HTTP, MCP, CLI, queue, authentication, and deployment integrations depend on the public facade. They cannot become dependencies of domain, ports, or application.

### Configuration and observability

Library configuration is passed through constructors. Importing the package does not read environment files, initialize clients, connect to services, or configure logging.

Application code emits sanitized events to an injected observer. The default observer is a no-op. Telemetry SDKs belong in observer adapters.

## Enforcement

- Import-boundary tests will reject inward dependencies on adapters or integrations.
- Base-package installation tests will assert that database, provider, and web dependencies are absent.
- Import smoke tests will run without environment variables or network access.
- Shared adapter contracts will define behavior independently of implementation.

## Consequences

- Domain and application tests can use deterministic fake ports without a database or network.
- Adapter packages may evolve independently while preserving the port contract.
- Mapping code is required at integration and persistence boundaries.
- Some provider-specific features cannot appear in the core API unless expressed as a neutral capability.

## Rejected alternatives

### Use the PostgreSQL repository as the core interface

Rejected because sessions, transactions, vector operators, and schema details would leak into every caller and prevent natural non-SQL adapters.

### Put provider clients directly in `MemoryEngine`

Rejected because model selection, retries, credentials, and lifecycle would become global core concerns.

### Use a web framework's models as domain objects

Rejected because serialization and HTTP validation are integration concerns and would add import-time and dependency coupling.
