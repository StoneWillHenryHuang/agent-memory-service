# ADR 0011: Optional localhost FastAPI reference over the public facade

- Status: Accepted
- Date: 2026-07-31

## Context

Users may need a concrete example of placing an HTTP boundary around the SDK,
but making a server part of the core would add framework dependencies and imply
authentication, deployment, and operational guarantees that the SDK does not
provide.

## Decision

Provide an optional `fastapi` extra and a non-production reference app that
depends only on the stable package-root `MemoryEngine`. Expose health, add,
recall, and delete use cases. Keep Pydantic HTTP schemas and explicit domain
mappers in the integration package rather than annotating domain types.

Use the app lifespan to open and close the injected engine when ownership is
enabled. Generate request IDs at the server, return bounded original error
envelopes without native exception text, allow only explicit localhost hosts,
deny CORS by default, and authorize only loopback IP peers by default. Provide
an injectable authorization protocol solely to show the boundary.

Disable served documentation routes so the registered HTTP route set stays
limited to the four reference endpoints. Keep programmatic OpenAPI generation
and document how to export it. Provide an original non-root Dockerfile that
installs only the reference extra and binds to loopback.

## Consequences

- The base wheel and root import remain free of FastAPI, Pydantic, and Uvicorn.
- HTTP and domain contracts can evolve independently and mapping remains visible.
- The example contains no admin API, custom client registry, Redis, custom
  header convention, environment loader, or production deployment template.
- The default hook and middleware are local safety defaults, not production
  authentication, rate limiting, or network policy.
- Deployers must design their own external security boundary and must not expose
  this reference app to an untrusted network.
