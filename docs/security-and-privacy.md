# Security and Privacy

Portable Memory Engine processes content that may be sensitive. It supplies
typed boundaries and conservative defaults, but it is not an authentication,
encryption, compliance, or data-governance product. The deploying application
owns those controls.

## Data surfaces

| Data | Where it can appear | Default handling |
|---|---|---|
| Conversation messages | Add command, prompt/model request | Used transiently; not stored as provenance by default |
| Memory content | Store, recall result, deletion target state | Stored by the selected adapter inside explicit scope |
| Semantic query | Recall request and embedding provider request | Not included in default observer events |
| Prompt and model output | Prompt/chat adapters and strict parser | Treated as untrusted content; excluded from default logs |
| Embeddings | Embedder and store | Validated for count, finite values, task, and dimension |
| Scope and provenance | Commands, records, store filters | Required for isolation; identifiers may still be sensitive |
| Credentials and URLs | Optional adapter configuration | Caller supplied; secret fields hidden from representations |
| Idempotency and barriers | Store control state | Content-free correctness state with deployment-owned retention |

Custom adapters, policies, observers, middleware, provider SDKs, and database
configuration can add data surfaces not controlled by the core package.

## Isolation and authorization

Every operation carries a complete tenant/subject/namespace scope. The store
must apply it during the native query or mutation; fetching broadly and
filtering afterward is not acceptable. Memory IDs, source values, session IDs,
HTTP clients, and MCP connection metadata are not authorization capabilities.

The base facade defaults to an allow policy so local examples are usable. A
production application must inject its own `AccessPolicy` and authenticate the
caller before constructing a command. Authorization should be tested for add,
recall, memory deletion, scope deletion, subject deletion, and session deletion
separately.

The FastAPI and MCP packages demonstrate hook boundaries only. Their local
defaults are not production authentication.

## Model and prompt boundary

Conversation text can contain prompt injection, malformed Unicode, oversized
content, or instructions to reveal other data. Provider responses can be
malformed, adversarial, or inconsistent with a requested response format.

- Keep system and user content separated through the prompt contract.
- Treat model output as data, not executable instructions.
- Require strict schema version, shape, enum, identifier, and size validation.
- Never use model output to select a tenant or authorize a memory target.
- Bound retries and response sizes, and preserve cancellation.
- Review provider retention, training, region, and telemetry behavior outside
  the SDK.

The default prompts add structure but do not eliminate prompt injection or data
exfiltration risk.

## Secrets and external connections

- Deliver provider and database credentials through application-owned secret
  management; the package does not read module-global settings.
- Do not place provider credentials in endpoint URLs or query strings, and do
  not place any credential in examples, exception text, or process arguments.
- A database connection URL may carry a password required by its driver. Resolve
  that value at runtime, pass it directly to the adapter, and never log or
  persist its unredacted form.
- Restrict caller-configured endpoints to an approved destination set. The
  OpenAI-compatible adapter sends its credential to the configured host.
- Use deployment-owned TLS verification, database roles, network policy, key
  rotation, and revocation.
- Keep development credentials and synthetic environments separate from
  production data.

Configuration representations and public exceptions are designed to omit
credential values, but surrounding libraries and custom code require their own
review.

## Logging and observability

The default observer is a no-op. The standard observer contract is intended for
bounded operational metadata such as operation, result category, counts,
duration, and memory kind. It excludes messages, memory content, queries,
prompts, responses, embeddings, metadata, tokens, credentials, and connection
strings.

Review all custom logging paths, including:

- provider SDK debug logging and HTTP tracing;
- database statement and parameter logging;
- web-server access/error logs and validation handlers;
- distributed tracing attributes and exception recording;
- metrics labels, dashboards, alerts, and support bundles.

Opaque request IDs should not encode scope or content. Sanitized errors should
retain their category while discarding native payloads.

## Deletion and retention

Typed delete commands affect the configured memory store and maintain barriers
against stale recreation. They do not prove removal from backups, replicas,
provider systems, caller logs, queues, caches, analytics, or observability
systems. Session contribution deletion does not automatically reconstruct
aggregate text.

A deployment needs a documented retention and deletion process for:

- visible memory records and embeddings;
- idempotency entries, watermarks, barriers, and contribution state;
- database backups, replicas, snapshots, exports, and disaster recovery;
- provider requests, responses, abuse monitoring, and model telemetry;
- application logs, traces, caches, and derived datasets.

Do not describe SDK deletion as regulatory erasure without end-to-end evidence
for the actual deployment.

## Dependency and artifact hygiene

- Install only required extras and pin reviewed artifacts.
- Inspect wheel and source-distribution contents before release.
- Scan source, generated documentation, examples, artifacts, and Git history for
  secrets and non-public identifiers.
- Review direct and transitive licenses and vulnerability reports.
- Keep build and publishing credentials out of the repository.
- Test clean installation and no-network examples from the built wheel.

Repository and build-artifact scanning are part of release hygiene. Each real
deployment still requires a review of its own data flows, dependencies, and
operational controls.

## Deployment review questions

Before handling real data, answer:

1. Who authenticates the caller and maps it to allowed memory scopes?
2. Where can messages, memories, prompts, embeddings, and queries be retained?
3. Which systems receive provider credentials and content?
4. How are database and transport encryption configured and verified?
5. What limits apply per caller, tenant, subject, and operation?
6. How are backups restored and deletions propagated?
7. Which logs, traces, metrics, and support tools can contain identifiers or
   content?
8. How are dependency updates, vulnerabilities, and incidents handled?

See [SECURITY.md](../SECURITY.md) for vulnerability-reporting status and the
[production guide](production.md) for the wider operational gap.
