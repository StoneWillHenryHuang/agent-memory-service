# Exception Guide

All expected public failures inherit from `MemoryEngineError`. Adapters must
translate native SDK, HTTP, and database failures before they cross this
boundary. Public messages must not include memory content, prompts,
credentials, connection strings, provider bodies, or native query text.

## Hierarchy and handling

| Exception | Meaning | Typical caller action |
|---|---|---|
| `DomainValidationError` | A public command/value violates an invariant | Fix the input; do not retry unchanged |
| `LifecycleError` | The facade or adapter is not open, or is closing | Correct lifecycle ownership/order |
| `AccessDeniedError` | The injected policy rejected the scoped operation | Return an authorization failure without widening scope |
| `CapabilityError` | The selected adapter cannot provide required semantics | Choose a capable adapter or change the explicit operation |
| `IdempotencyConflictError` | A scoped key was reused with another payload | Generate a new key or restore the original payload |
| `StaleEventError` | Event time is at/before an accepted watermark or barrier | Reconcile ordering; do not rewrite the timestamp blindly |
| `ConflictError` | An optimistic concurrency condition lost | Retry only with a fresh read and a bounded policy |
| `ProviderUnavailableError` | Model/embedding provider is temporarily unavailable | Apply bounded retry/backoff outside the core |
| `ProviderParseError` | Untrusted provider output failed its public schema | Inspect sanitized reason/model configuration; do not persist output |
| `PromptResolutionError` | No prompt matched the public lookup key | Register or select an explicit prompt |
| `ProviderError` | Other sanitized provider failure | Treat as provider failure; retry only when policy says it is safe |
| `StoreUnavailableError` | Store is temporarily unavailable | Apply bounded retry/backoff if the command is idempotent |
| `StoreError` | Other sanitized persistence failure | Investigate the adapter/deployment without exposing native details |

Catch the narrowest useful class. `ProviderUnavailableError` is also a
`ProviderError`; `StoreUnavailableError` is also a `StoreError`; conflict
subtypes are also `ConflictError`.

## Add partial results

`MemoryEngine.add()` intentionally converts expected per-kind provider, parse,
store, validation, stale-event, idempotency, and CAS failures into structured
`KindAddResult` entries so summary, fact, and profile work can succeed or fail
independently. Inspect `AddMemoryResult.status`, each kind status, and bounded
issue code.

Failures outside a per-kind attempt still raise. Examples include an invalid
command object, lifecycle misuse, access denial, an unsupported requested kind,
or an observer failure. Recall and deletion return their result or raise a
public exception directly.

## Cancellation and unknown defects

Async cancellation is not converted into `MemoryEngineError`; it propagates so
the caller's task and shutdown policy remain authoritative. Do not catch
`BaseException` around normal SDK calls. Unexpected programming defects are not
repackaged as successful or partial results.
