# ADR 0010: One stable root facade with explicit dependencies

- Status: Accepted
- Date: 2026-07-31

## Context

The application layer has separate add, recall, and deletion services. Requiring
ordinary users to assemble and coordinate all three lifecycles would expose
internal architecture and make shared-resource ownership ambiguous.

## Decision

Expose `portable_memory_engine.MemoryEngine` as the stable root facade. It
composes the three application services, requires caller-selected store, chat
model, embedder, and prompt provider, and manages each distinct injected async
resource exactly once by default. It delegates behavior without adding provider,
database, transport, authentication, or configuration policy.

Keep optional adapters in explicit subpackages. Root imports include only the
dependency-free facade, common immutable values, protocols, sanitized errors,
and local reference components. Protect the exact export tuple and the README
quickstart with executable tests.

## Consequences

- A common application needs one async context manager for add, recall, delete,
  and shared resources.
- Advanced users can still use the application services and protocols directly.
- `owns_resources=False` is explicit and shifts full injected-resource lifecycle
  responsibility to the caller.
- Adding a root export becomes a compatibility decision even during v0.x.
