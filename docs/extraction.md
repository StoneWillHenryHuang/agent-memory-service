# Extraction application service

`portable_memory_engine.application.MemoryEngine` is the async orchestration
service for built-in summary, fact, and profile memory extraction. It depends on
ports supplied by the caller and does not choose a model, endpoint, database,
credential source, or cloud provider.

## Responsibilities

The default implementation keeps each responsibility replaceable:

- `SummaryExtractor`, `FactExtractor`, and `ProfileExtractor` perform one
  strictly parsed prompt call for their memory kind.
- `MemoryUpdater` validates fact actions and performs guarded writes.
- `FreshnessGuard` checks event time before model work and again before writes.
- `DefaultIdentityStrategy` creates versioned deterministic IDs from complete
  scope and the logical summary session, canonical fact text, or profile name.
- `MemoryEngine.add` authorizes, applies input limits, coordinates the
  strategies, retries CAS conflicts, emits content-free events, and assembles
  per-kind results.

Only user-authored messages are included in default fact extraction. Summary
and profile extraction can use the complete supplied conversation. Prompt
rendering uses bounded JSON values and model output is accepted only after the
versioned parser validates it.

## Fact actions

The fact update model receives temporary aliases such as `memory-0000`, never
record IDs. Its output may request `add`, `update`, `delete`, or `noop`.
Unknown aliases, repeated targets, inconsistent fields, identity collisions,
and malformed output are rejected before persistence. Operations are applied
in order and stop at the first failure, so the result can report an already
committed prefix without concealing the failure.

## Freshness, concurrency, and replay

Every write uses the command event timestamp, a deterministic child
idempotency key, a canonical caller-input digest, and compare-and-swap state.
The engine re-reads and recomputes after a conflict up to
`ExtractionLimits.max_conflict_retries`; it never falls back to an unconditional
overwrite. A direct replay with the same command key and input returns the
store's replay outcome without creating another record. Reusing the same key
with different input conflicts for an already addressed mutation.

The memory-store port does not expose a separate command-result lookup. A replay
therefore may repeat model and embedding calls before the atomic store replay is
observed, and commands that produced no mutation have no durable replay record.
Callers that require a durable batch-result ledger should place one outside the
core service.

## Results and observability

`AddMemoryResult` contains one `KindAddResult` per requested built-in kind and
derives an overall `succeeded`, `partial`, or `failed` state. Expected provider,
parse, stale-event, store, conflict, and idempotency failures use bounded issue
codes rather than raw exception text. One kind failing does not roll back a
different kind that has already succeeded.

Built-in observation events contain operation, outcome, timing, kind, and item
count only. They do not contain scope, message text, memory content, rendered
prompts, raw model responses, IDs, or idempotency keys. Custom ports remain
responsible for applying the same privacy rule.

## Resource ownership and limits

Use `async with engine` or call `open()` and `close()`. By default, the engine
opens and closes injected lifecycle resources and waits for active additions
before closing. Set `owns_resources=False` when the caller manages them.

`ExtractionLimits` bounds message count and characters, rendered prompt size,
existing fact count and content, and CAS retries. Required storage capabilities
are validated when the engine is constructed, before model work begins.
