# Production Readiness Guide

Portable Memory Engine is a pre-release library candidate. The core behavior
and reference adapters are tested, but the repository does not claim that any
included configuration is production-ready. A production decision belongs to
the deploying application and its operating environment.

## Reference versus production

| Included component | What it demonstrates | Production work not supplied |
|---|---|---|
| Core SDK and facade | Typed in-process add, recall, delete, lifecycle, and failure contracts | Application authentication, authorization policy, quotas, deployment, incident response |
| In-memory store | Complete deterministic behavior in one process | Durability, multi-process coordination, backup, restore, capacity management |
| PostgreSQL adapter | Durable schema, migrations, exact search, atomic correctness contracts | Database provisioning, TLS policy, encryption, HA, backup/restore, monitoring, scaling, index tuning |
| Provider adapters | Explicit configuration, bounded mapping, validation, sanitized failure boundary | Credential delivery, endpoint allowlisting, data-processing review, quotas, regional and retention policy |
| FastAPI reference | Local transport schemas and facade mapping | Production authn/authz, TLS/proxy trust, rate limits, body limits, timeouts, audit, deployment |
| MCP reference | Local stdio tools and authorization-hook shape | Network transport security, OAuth, client trust, rate limits, hosting and operations |

Changing a reference transport from loopback or stdio to a network listener does
not fill these gaps.

## Deployment responsibilities

### Identity and authorization

- Authenticate callers before they reach memory operations.
- Derive tenant, subject, and namespace through an application-owned policy;
  never trust a caller to authorize its own scope.
- Inject an `AccessPolicy` that evaluates every add, recall, and delete target.
- Treat subject-wide deletion as a separate privileged operation.
- Test cross-tenant and cross-namespace denial at both application and storage
  boundaries.

### Data protection and lifecycle

- Classify messages, memories, prompts, embeddings, and provider payloads under
  the application's data policy.
- Encrypt transport and storage using deployment-owned controls.
- Decide retention for records, idempotency entries, watermarks, deletion
  barriers, backups, replicas, provider logs, and operational telemetry.
- Document backup restore and deletion propagation. SDK deletion covers only
  the configured store boundary and is not a regulatory-erasure guarantee.
- Keep environments and tenants separated according to the application's risk
  model; a namespace is a logical boundary, not infrastructure isolation.

### Database operations

- Keep `auto_migrate=False` for ordinary application startup unless the
  deployment deliberately accepts startup migrations.
- Run migrations as a separately authorized operation and test them against a
  disposable copy before production use.
- Back up and verify restore before a schema or embedding migration.
- Configure connection limits, statement timeouts, lock monitoring, TLS,
  credentials, and database roles outside the SDK.
- Benchmark with representative synthetic data. Exact vector search is the
  portable default; the project publishes no throughput or latency guarantee.

### Provider operations

- Supply provider URLs, projects, regions, models, dimensions, credentials,
  timeout, retry, and quota policy explicitly.
- Confirm the provider's data retention and training terms for the selected
  service and account; the SDK cannot infer them.
- Restrict custom endpoint URLs to trusted destinations to reduce credential
  forwarding and server-side request risks.
- Monitor bounded failure categories rather than recording raw requests or
  responses.
- Plan re-embedding and index replacement before changing an embedding model or
  dimension.

### Limits and resilience

- Apply caller, tenant, subject, request-size, concurrency, and rate limits at
  the external boundary.
- Set end-to-end deadlines that include model calls, embeddings, database work,
  and retries; preserve cancellation.
- Decide whether partial per-kind add results are acceptable for each workflow.
- Define retry rules for typed conflicts and temporary availability failures.
  Never retry validation or authorization failures blindly.
- Test shutdown while work is active and confirm ownership of every shared
  adapter client.

### Observability

- Keep the default content-free observer contract. Record operation names,
  bounded result categories, counts, and durations rather than payloads.
- Review custom adapters, policies, middleware, exception handlers, tracing,
  database logging, and provider SDK telemetry for content or credential leaks.
- Use opaque correlation IDs that do not encode tenant, subject, session, or
  memory content.
- Separate health from capabilities: an adapter can support an operation while
  temporarily being unavailable.

## Release gate for an application

Before production use, the deploying team should have evidence for all of the
following:

- a pinned, reviewed package artifact and dependency inventory;
- application-owned authentication and scoped authorization tests;
- database migration, backup, restore, rollback, and deletion runbooks;
- provider and data-processing approval for every configured endpoint;
- tested resource limits, deadlines, retries, cancellation, and shutdown;
- synthetic load and failure tests in a reproducible environment;
- secret-safe logs, traces, errors, dashboards, and incident tooling;
- retention and deletion behavior across stores, backups, replicas, providers,
  and telemetry;
- vulnerability intake, dependency update, and incident-response ownership.

This list is a technical starting point, not a certification checklist. See
[security and privacy](security-and-privacy.md),
[migration and versioning](migration-and-versioning.md), and
[compatibility and limitations](compatibility.md).
