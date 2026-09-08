# FAQ and Limitations

## Is this project production-ready?

No. It is a `0.1.0.dev0` pre-release candidate. Core correctness contracts and
reference adapters are tested, but production authentication, infrastructure,
operations, independent security review, and release licensing are not
supplied. Start with the [production gap](production.md).

## Is it a hosted memory service?

No. It is an async Python library. The application injects a store, chat model,
embedder, and prompt provider and owns its external boundary.

## Does the base package call a model or database?

No. The base installation has no runtime dependency and selects no endpoint.
Its scripted model, deterministic embedder, and in-memory store are local test
and example components.

## Which memory types are included?

The defaults implement summary, fact, and profile. Custom kinds are an
extension point, but the default extraction strategies do not automatically
understand them.

## What isolates one user's memory from another?

Every operation uses `MemoryScope`: optional tenant, required subject, and
required namespace. The store enforces the complete scope. A memory ID, source,
session ID, HTTP client, or MCP context is not a substitute.

## Is `tenant_id=None` a wildcard?

No. It denotes a deliberately configured single-tenant boundary. It must match
other `None`-tenant records and never crosses into a named tenant.

## Does semantic recall fall back to recency?

No. If the store lacks vector search or the embedding configuration is
incompatible, semantic recall raises a capability error.

## Is total count always available?

No. A result distinguishes exact, approximate, and unavailable counts. The
value zero is used only for a known empty result.

## Does idempotency guarantee exactly-once delivery?

No. It makes replay of the same scoped key and canonical payload stable inside
the configured store. Queue delivery, cross-system transactions, and
exactly-once processing are outside the package.

## Can old events overwrite newer memories?

The included stores enforce event-time freshness before persistence, and the
application checks before model work and again before write. Custom adapters
must provide the same declared semantics.

## What happens when two writes conflict?

Writes use opaque versions and compare-and-swap. The application can re-read
and recompute for a bounded number of attempts, then surfaces a typed conflict.
It never silently switches to an unconditional overwrite.

## Does delete mean the data is gone everywhere?

No. It covers the selected memory store and its public barriers. It does not
claim deletion from backups, replicas, providers, logs, telemetry, caches, or
other systems.

## Does session deletion rebuild shared summaries or facts?

No. It removes or tombstones the selected session's contributions according to
kind, but does not automatically subtract text from an aggregate or regenerate
it. A strictly newer explicit write is required for regeneration.

## Can I change embedding model or dimension in place?

Not safely. The PostgreSQL adapter rejects a dimension mismatch. Plan a
re-embedding and index migration that preserves scope and supports rollback.

## Does OpenAI-compatible mean every compatible endpoint works?

No. The adapter implements a bounded public Chat Completions shape and detects
specific structured-output behavior. Test the selected endpoint and review its
authentication, retention, limits, and error behavior.

## Are the FastAPI and MCP examples deployable services?

No. FastAPI is a localhost reference and MCP is a stdio reference. They omit
production authentication, authorization policy, rate limits, proxy/TLS trust,
audit, monitoring, and deployment controls.

## Does the SDK prevent prompt injection?

No. Default prompts separate roles and all output is parsed strictly, but those
controls do not eliminate prompt injection or provider-side data risks.

## Does the project claim compliance or performance targets?

No. It makes no regulatory certification, erasure, latency, throughput,
availability, scalability, or cost claim. Those properties depend on the
actual application, adapters, providers, data, and infrastructure.

## Which Python versions and APIs are stable?

The candidate requires Python 3.12 or later versions included in its tested
matrix and exposes an async-only root facade. Because this is v0.x, pin exact
artifacts and read migration notes before upgrading.

## Where should I start?

Run the [five-minute quickstart](../README.md#five-minute-in-memory-quickstart),
then read [concepts](concepts.md), [adapter selection](adapters.md), and
[architecture](architecture.md). Use the [synthetic sample app](../examples/sample_app.py)
to see add, recall, and delete in one offline flow.
