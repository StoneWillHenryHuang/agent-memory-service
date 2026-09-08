# Public API Reference

The stable v0.1 import surface is `portable_memory_engine`. Applications can use
the common add, recall, and delete path without importing implementation
modules.
Optional provider adapters remain in explicit subpackages so importing this
facade never requires their SDKs.

## `MemoryEngine`

```python
from portable_memory_engine import MemoryEngine

engine = MemoryEngine(
    store=my_store,
    chat_model=my_chat_model,
    embedder=my_embedder,
    prompt_provider=my_prompt_provider,
)
```

All four dependencies are required and caller-selected. They implement the
public `MemoryStore`, `ChatModel`, `Embedder`, and `PromptProvider` protocols.
The facade does not select a backend, endpoint, model, region, credential
source, or prompt override.

The default `owns_resources=True` means one facade opens each distinct injected
resource once, waits for active operations during shutdown, and closes resources
in reverse dependency order. Prefer the async context manager:

```python
async with engine:
    add_result = await engine.add(add_command)
    recall_result = await engine.recall(recall_request)
    delete_result = await engine.delete(delete_command)
```

`open()` and `close()` are idempotent. With `owns_resources=False`, the caller
must open every injected store/model/embedder/prompt/observer before the facade
and close them after the facade. Operations before open or while closing raise
`LifecycleError`.

## Commands and results

| Method | Input | Output | Notes |
|---|---|---|---|
| `add` | `AddMemoryCommand` | `AddMemoryResult` | Complete scope, idempotency key, event time, messages, and selected kinds |
| `recall` | `RecallRequest` wrapping `MemoryQuery` | `RecallResult` | Recency by default; semantic mode is explicit and requires vector capability |
| `delete` | A typed delete command | `DeleteResult` | Supports memory ID, scope, subject, or session targets according to store capabilities |

All commands and returned records are immutable public values. Every operation
uses a complete scope or subject boundary. `AddMemoryResult` may contain
per-kind partial outcomes; callers should inspect its aggregate and kind
statuses rather than assuming every accepted command wrote every kind.

## Dependency-free reference components

The package root exposes these components for examples, tests, and local
development:

- `InMemoryMemoryStore`: process-local, non-durable reference store;
- `ScriptedChatModel`: fixed synthetic model responses, no network;
- `DeterministicEmbedder`: stable hash vectors with no semantic-quality claim;
- `DefaultPromptProvider`: generic versioned prompts;
- `FixedClock`: deterministic event processing time.

The same constructor accepts custom implementations. Advanced implementers can
use the public protocol definitions under `portable_memory_engine.ports`, the
domain values under `portable_memory_engine.domain`, and the shared
[adapter contracts](adapter-contracts.md). Provider implementations available
today are limited to the documented PostgreSQL, OpenAI-compatible, and Google
Vertex AI optional adapters; no other backend or provider is implied.

## Stable root exports

The exact root export tuple is protected by a compatibility test. It contains:

- the facade and version;
- common add, recall, and typed deletion commands/results;
- common scope, query, record, embedding, and message values;
- the four injected dependency protocols and request/response values needed by
  simple custom providers;
- dependency-free reference components;
- the sanitized public exception hierarchy.

Specialized strategies, migration helpers, provider configuration, parser
internals, and database models are intentionally not root exports.

## Optional integration namespaces

Optional integrations are not package-root exports:

- `portable_memory_engine.integrations.fastapi` contains the localhost FastAPI
  reference factory and its reference-only security configuration;
- `portable_memory_engine.integrations.mcp` contains the stdio-only MCP server
  factory, runner, lifecycle configuration, and injected authorization protocol.

Both integrations accept only the stable package-root `MemoryEngine`. Their
transport schemas and security hooks are separate contracts and do not expand
the root facade or make either framework a base dependency.
