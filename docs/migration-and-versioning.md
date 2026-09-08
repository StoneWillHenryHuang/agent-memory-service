# Migration and Versioning

The current package version is `0.1.0.dev0`. It is a pre-release candidate with
no published compatibility history. Until a stable release policy is adopted,
pin exact artifacts and assume that a v0.x update can require code or data
changes.

## Separately versioned contracts

Portable Memory Engine has several compatibility surfaces. A shared package
version does not make their serialized representations interchangeable.

| Surface | Current signal | Compatibility rule |
|---|---|---|
| Package-root Python API | Exact export snapshot in tests | Additions or removals require review and release notes |
| Domain behavior | ADRs and typed values | Scope, identity, freshness, OCC, deletion, and result changes require migration guidance |
| Storage port | Contract version `1.1` | Adapter must match the supported contract and pass its declared capability tests |
| PostgreSQL schema | Independent Alembic history beginning at `0001` | Published revisions are immutable; new changes use new revisions |
| Prompt output | `schema_version` in each strict output shape | Parser and prompt must agree; unsupported output fails closed |
| Identity strategies | Explicit strategy version in deterministic identities | A strategy change may create different logical record IDs |
| Embeddings | Configured model, task, and dimension | Incompatible vectors require re-embedding and index migration |
| HTTP and MCP schemas | Integration-owned schemas | They are not Python-domain serialization and may version independently |

Pickle, `repr`, dataclass field order, ORM objects, provider responses, and
internal module paths are not stable interchange formats.

## Upgrade workflow

For each package update:

1. Read release notes, ADR changes, and the compatibility document.
2. Compare root imports and optional-extra requirements used by the application.
3. Run unit tests against the new artifact with fake providers and a disposable
   store.
4. Run the shared storage contract against every custom adapter.
5. Inventory changes to scope, identity, event-time rules, deletion, prompt
   schemas, embeddings, and transport schemas.
6. Back up durable state and verify restore before applying database or vector
   changes.
7. Rehearse forward migration and application rollback in a non-production
   environment using synthetic data.
8. Deploy application and migration changes in an order that both versions can
   tolerate, or take an explicit maintenance window.
9. Verify counts, capability reports, health, and bounded error categories
   without logging memory content.

There is no automatic migration from an unrelated or legacy memory schema. The
PostgreSQL adapter starts from its own history.

## PostgreSQL schema changes

The adapter does not run migrations by default. Use the programmatic migration
helper described in the [PostgreSQL guide](postgresql.md) under a separately
authorized database role.

- Apply migrations before starting code that requires the new schema.
- Do not edit an already published revision; add a new revision.
- Test upgrade against an empty database and a restored disposable snapshot.
- Treat downgrade support as revision-specific, not guaranteed recovery.
- Prefer restoring a verified backup when a lossy data transformation cannot be
  reversed safely.
- Keep application startup on `auto_migrate=False` unless startup migration is
  an explicit operational decision.

The initial schema stores one configured embedding dimension. Opening the same
database with a different dimension fails rather than mixing incompatible
vectors.

## Embedding model or dimension migration

The project does not include an online re-embedding command. A deployment-owned
plan should:

1. freeze the source record set or capture changes during migration;
2. create storage separated from the old vector representation;
3. recompute every document vector with the new model, task mapping, and
   dimension;
4. validate count, finite values, dimension, and scope preservation;
5. build and verify the new search path using synthetic queries;
6. switch reads and writes deliberately;
7. retain or remove old vectors according to rollback and retention policy.

Do not change the configured dimension in place and assume existing vectors are
compatible. Model names alone are not a compatibility guarantee.

## Identity and prompt migration

Changing fact canonicalization, summary boundaries, profile names, or identity
strategy versions can change deterministic IDs. Choose one of these policies
explicitly:

- keep the old strategy for existing and new records;
- let old and new strategy versions coexist and query both;
- transform records to new IDs with collision and replay handling;
- rebuild from an authorized source of truth.

Changing prompt text without changing its public version weakens provenance.
When output shape changes, publish a new prompt/schema version, add its parser,
and decide whether old memories remain valid or need regeneration. Never make a
parser silently accept an unknown schema.

## Scope and deletion migration

Changes to tenant, subject, namespace, source, or session mapping can move the
effective isolation boundary. Such a change requires an application-owned data
mapping, cross-scope collision analysis, authorization review, and rollback
plan. A memory ID cannot be used to infer the destination scope.

Preserve freshness watermarks, idempotency records, deletion barriers, and
session contribution state when moving records. Copying only visible memory
rows can allow late events to recreate deleted or stale content.

## Rollback

Application rollback and data rollback are separate decisions. Before an
upgrade, record:

- the previous tested artifact and dependency set;
- whether the old code can read the new schema and contract version;
- whether a migration is additive, destructive, or irreversible;
- how writes made after migration will be handled;
- how provider or embedding changes affect stored state.

Do not automatically downgrade a database merely because application rollout
failed. Stop writes, assess compatibility, and choose either application
rollback, forward fix, or verified data restore.

Before the first public release, every compatibility change must be added to a
changelog. See [concepts](concepts.md), [adapter contracts](adapter-contracts.md),
and [compatibility](compatibility.md).
