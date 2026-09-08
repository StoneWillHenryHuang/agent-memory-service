# ADR 0002: Complete Scope Is the Isolation Boundary

- Status: Accepted
- Date: 2026-07-30

## Context

Memory IDs, source channels, and session identifiers are convenient selectors, but none of them is a safe tenancy boundary. A storage adapter must be able to enforce isolation without relying on application-side filtering after a broad query.

v0.1 must work for single-tenant applications while preserving a model that can safely represent multiple tenants.

## Decision

Every read, write, search, freshness check, ID delete, scope delete, and session
delete carries a complete `MemoryScope`:

```text
MemoryScope(tenant_id: str | None, subject_id: str, namespace: str)
```

### Field roles

- `tenant_id` is an optional tenant isolation value. `None` is a concrete single-tenant partition and never means “all tenants.”
- `subject_id` identifies the person or entity the memory concerns. It is required.
- `namespace` identifies the application or logical memory space. It is required and may explicitly default to `default`.
- `source` is provenance only.
- `session_id` is correlation and filtering only.

The record identity is `(tenant_id, subject_id, namespace, memory_id)`. A memory ID alone is insufficient for access.

### Validation

Scope strings are trimmed, non-empty, bounded in length, and validated against a conservative public character policy defined by the domain layer. The original value is not silently rewritten beyond documented Unicode normalization.

Wildcard scope values are not part of the domain model. The only base-package
cross-namespace operation is the explicit `MemorySubject` deletion boundary
defined by ADR 0005. It fixes tenant and subject, requires separate policy and
store capabilities, and does not authorize cross-tenant access. Other
administrative cross-scope operations belong in an integration.

### Storage enforcement

Adapters include the complete scope in keys, uniqueness constraints, reads,
search predicates, compare-and-swap conditions, watermarks, idempotency records,
and scoped deletes. Subject deletion instead includes the complete
`MemorySubject` tenant-and-subject boundary in its predicate and control state.

Scope filtering must be executed by the store. Fetching broad results and filtering them in application memory is a contract violation.

### Access policy

An injected `AccessPolicy` may narrow the scopes a caller can read, write, or
delete. Subject-wide deletion supplies a `MemorySubject`; every other operation
supplies a complete `MemoryScope`. Policy decisions do not return HTTP
responses.

Policy approval does not remove the scope predicate from storage operations. Defense in depth requires both a policy decision and scoped store access.

## Consequences

- The same memory ID can safely exist in different scopes.
- Single-tenant deployments retain explicit subject and namespace isolation.
- Every adapter needs compound keys or an equivalent native isolation mechanism.
- Bulk operations must state their scope and cannot treat missing tenant values as wildcards.
- Moving a record between scopes is a delete-and-create operation with explicit policy, not a metadata update.

## Rejected alternatives

### Use `source` as namespace and tenant

Rejected because provenance, application grouping, and authorization change for different reasons and need independent evolution.

### Treat memory IDs as globally authorized capabilities

Rejected because IDs can leak, collide, or be guessed, and because global lookup makes tenant predicates easy to omit.

### Add tenant filtering after retrieval

Rejected because out-of-scope content would already have crossed the store boundary and could leak through counts, logs, or errors.
