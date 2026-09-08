# ADR 0009: Google Vertex AI as the provider embedding reference

- Status: Accepted
- Date: 2026-07-31

## Context

The public SDK needs at least one production-shaped embedding adapter while its
base install and core architecture remain provider-neutral. The adapter must
support explicit model, dimension, region, batching, document/query task hints,
sanitized errors, and testability without a cloud account.

## Decision

Provide `GoogleVertexEmbedder` as an optional adapter backed by the public Google
Gen AI SDK in Vertex AI mode. Distribute it only through the independent
`google-vertex` extra. Require the caller to provide project, region, model, and
dimension; keep task-hint mapping and SDK response handling inside the adapter.

Validate response cardinality, finiteness, and dimension before returning an
immutable domain embedding. Treat the embedding model and dimension as index
compatibility inputs that require explicit re-embedding and re-indexing when
changed.

## Consequences

- Base and core modules have no cloud SDK dependency.
- Installing this provider does not install another model or storage provider.
- Tests can cover all adapter behavior with an injected fake async client.
- Callers retain responsibility for credentials, model availability, regional
  policy, quotas, and index migration.
