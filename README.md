# Portable Memory Engine

Portable Memory Engine is an async-first Python SDK for scoped,
provider-neutral conversational memory.

## Why this project

Conversational memory sits at an awkward boundary: model output is untrusted,
storage must isolate subjects, retries must not duplicate writes, old events
must not overwrite newer state, and applications should be able to change a
database or provider without rewriting memory behavior.

This project keeps those concerns in a typed in-process engine. Applications
inject their store, chat model, embedder, prompt provider, policy, and observer;
the engine supplies explicit add, recall, delete, lifecycle, idempotency,
freshness, and concurrency contracts. It is a library rather than a hosted
service.

## Project status

The repository is at `0.1.0.dev0`. Add, recall, typed
deletion, the stable facade, dependency-free reference components, PostgreSQL,
OpenAI-compatible chat, Google Vertex embeddings, and local FastAPI/MCP
examples are implemented and tested. It is a development-stage library for
technical evaluation and portfolio demonstration, not a supported production
service.

Evaluate deployment-specific security, operations, and performance requirements
before using it with production data.

## Capabilities

| Area | Included behavior |
|---|---|
| Memory | Summary, fact, profile, replaceable strategies, strict model-output parsing |
| Correctness | Scoped idempotency, event-time freshness, compare-and-swap, bounded conflict retry, partial results |
| Recall | Recency and semantic modes, store-side filters, stable pagination, optional reranking |
| Deletion | Memory, scope, subject, and session-contribution targets with barriers and structured counts |
| Storage | Concurrent in-memory reference and optional PostgreSQL + pgvector adapter |
| Providers | Scripted/deterministic local components plus optional OpenAI-compatible chat and Google Vertex embeddings |
| Integration examples | Localhost FastAPI reference and stdio-only MCP reference |

No synchronous facade, production service, automatic provider selection,
arbitrary database support, compliance guarantee, or performance guarantee is
included.

## Documentation

Start with the [documentation map](docs/index.md), [concepts](docs/concepts.md),
[architecture](docs/architecture.md), and
[adapter matrix](docs/adapters.md). Before real deployment, read the
[production gap](docs/production.md),
[security and privacy guide](docs/security-and-privacy.md),
[migration and versioning guide](docs/migration-and-versioning.md), and
[FAQ and limitations](docs/faq.md).

## Requirements

- Python 3.12 or later

The base package has no runtime dependencies.

Install the base package directly from a source checkout:

```bash
python -m pip install .
```

Install the durable storage extra only when needed:

```bash
python -m pip install ".[postgres]"
```

Install the generic chat-model extra independently when needed:

```bash
python -m pip install ".[openai-compatible]"
```

Install the Google Vertex AI embedding extra independently when needed:

```bash
python -m pip install ".[google-vertex]"
```

Install the non-production FastAPI reference extra independently when needed:

```bash
python -m pip install ".[fastapi]"
```

Install the stdio-only MCP reference extra independently when needed:

```bash
python -m pip install ".[mcp]"
```

## Five-minute in-memory quickstart

This complete example uses only the stable package-root API. It needs no `.env`,
credentials, database, or network access:

```python
import asyncio
from datetime import UTC, datetime

from portable_memory_engine import (
    AddMemoryCommand,
    AddResultStatus,
    ChatResponse,
    ConversationMessage,
    DefaultPromptProvider,
    DeterministicEmbedder,
    FixedClock,
    InMemoryMemoryStore,
    MemoryEngine,
    MemoryKind,
    MemoryQuery,
    MemoryScope,
    RecallRequest,
    ScriptedChatModel,
)


async def main() -> None:
    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    scope = MemoryScope(subject_id="synthetic-traveler", namespace="quickstart")
    engine = MemoryEngine(
        store=InMemoryMemoryStore(),
        chat_model=ScriptedChatModel(
            (
                ChatResponse(
                    content=(
                        '{"schema_version":"fact.v1","facts":'
                        '["The synthetic traveler prefers morning trains."]}'
                    ),
                    model_id="scripted-quickstart-v1",
                ),
                ChatResponse(
                    content=(
                        '{"schema_version":"update.v1","operations":['
                        '{"event":"add","memory_id":null,"content":'
                        '"The synthetic traveler prefers morning trains."}]}'
                    ),
                    model_id="scripted-quickstart-v1",
                ),
            )
        ),
        embedder=DeterministicEmbedder(dimension=8),
        prompt_provider=DefaultPromptProvider(),
        clock=FixedClock(event_time),
    )

    async with engine:
        added = await engine.add(
            AddMemoryCommand(
                scope=scope,
                messages=(
                    ConversationMessage(
                        role="user",
                        content="I prefer morning trains for this synthetic trip.",
                    ),
                ),
                idempotency_key="quickstart-add-1",
                event_timestamp=event_time,
                kinds=frozenset({MemoryKind.FACT}),
                source="quickstart",
                session_id="synthetic-session-1",
            )
        )
        recalled = await engine.recall(RecallRequest(MemoryQuery(scope=scope)))

    assert added.status is AddResultStatus.SUCCEEDED
    assert [match.record.content for match in recalled.items] == [
        "The synthetic traveler prefers morning trains."
    ]


asyncio.run(main())
```

The constructor is the composition boundary: replace the four injected
`store`, `chat_model`, `embedder`, and `prompt_provider` values without changing
application code. See [the executable copy](examples/in_memory_quickstart.py),
[API reference](docs/api-reference.md), and [exception guide](docs/exceptions.md).
The in-memory adapter and scripted providers are process-local development
tools; they make no durability or semantic-quality claim.

## Complete synthetic sample app

Run an offline, self-checking add → recall → delete workflow through the stable
facade:

```bash
python examples/sample_app.py
```

The app uses only synthetic identifiers and content, deterministic providers,
and the in-memory store. It opens no network connection, reads no environment
variable, writes no file, and exits quietly when its assertions pass. See the
[source](examples/sample_app.py) and [architecture walkthrough](docs/architecture.md).

## Generic prompts and strict parsing

Resolve the dependency-free generic prompts and parse a synthetic response:

```bash
python examples/prompt_parsing.py
```

See [the prompt and schema contract](docs/prompts.md) and
[the complete example](examples/prompt_parsing.py). The prompt instructions add
a basic injection-resistance layer; they are not a complete defense, and raw
model output must always pass the strict parser before use.

## Extraction quickstart

Run a complete summary, fact, and profile extraction flow with a local scripted
chat model and the dependency-free adapters:

```bash
python examples/extraction_quickstart.py
```

See [the extraction application contract](docs/extraction.md) and
[the complete example](examples/extraction_quickstart.py). Production callers
must inject their own chat model and embedding adapters; the package does not
select a model, endpoint, credential source, or cloud service.

## Recall quickstart

Run recency and semantic recall over the dependency-free in-memory adapter:

```bash
python examples/recall_quickstart.py
```

See [the recall application contract](docs/recall.md) and
[the complete example](examples/recall_quickstart.py). Every query carries a
complete scope, and semantic recall fails explicitly instead of falling back
when vector search is unavailable.

## Deletion quickstart

Run a session contribution deletion over synthetic summary, fact, and profile
records:

```bash
python examples/deletion_quickstart.py
```

See [the deletion application contract](docs/deletion.md) and
[the complete example](examples/deletion_quickstart.py). The SDK only promises
deletion inside the configured store boundary; it does not claim removal from
backups, provider systems, caller telemetry, or external replicas.

## PostgreSQL + pgvector

The durable reference adapter runs on ordinary PostgreSQL with the public
pgvector extension. It uses exact cosine search by default, validates one
configured embedding dimension at startup, and never runs migrations unless the
caller requests that behavior explicitly.

See [the PostgreSQL adapter guide](docs/postgresql.md) for migration, lifecycle,
container, schema, pooling, and integration-test instructions. No AlloyDB, cloud
SDK, or provider-specific vector index is required.

## OpenAI-compatible chat model

The optional reference adapter targets the public OpenAI-style Chat Completions
HTTP shape. The caller must provide the API root, API key, and model; the
adapter has no endpoint, model, gateway, or environment defaults.

See [the OpenAI-compatible adapter guide](docs/openai-compatible.md) for
structured-output detection, retries, `Retry-After`, cancellation, lifecycle,
usage mapping, and the custom-URL trust boundary. All adapter tests use mock
transport and make no model request.

## Google Vertex AI embeddings

The optional reference adapter uses the public Google Gen AI SDK with Vertex
AI. The caller supplies the project, region, model, output dimension, batch
size, and credentials policy; the adapter has no cloud, model, or dimension
defaults.

See [the Google Vertex AI embedding guide](docs/google-vertex-embeddings.md) for
task-hint mapping, batching, lifecycle, validation, and the required re-index
plan when a model or dimension changes. All adapter tests use an injected fake
client and require no cloud account.

## FastAPI reference service

The optional reference app exposes only health, add, recall, and delete use
cases over the stable `MemoryEngine` facade. Its default authorization accepts
loopback IP clients only, its CORS allowlist is empty, and its trusted hosts are
limited to localhost. It is an integration example, not a deployable production
service or authentication system.

Run the synthetic app on loopback only:

```bash
uvicorn examples.fastapi_reference:app \
  --host 127.0.0.1 --port 8000 --no-access-log
```

See the [FastAPI reference guide](docs/fastapi-reference.md) for OpenAPI export,
curl examples, the authorization hook, lifecycle ownership, error envelopes,
security defaults, and the non-root reference Dockerfile.

## MCP reference integration

The optional MCP reference exposes exactly `add_memory`, `recall_memory`, and
`delete_memory` over process stdio. Every call supplies a complete scope or
subject boundary, authorization is an injected hook, and each connection gets
an isolated opaque context. It provides no HTTP transport, OAuth, API-key
handling, resources, prompts, sampling, roots, or production deployment policy.

Run the synthetic server from a source checkout:

```bash
python examples/mcp_reference.py
```

See the [MCP reference guide](docs/mcp-reference.md) for client configuration,
tool contracts, lifecycle ownership, error behavior, isolation, and security
limitations.

## Development setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Run the local quality checks:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/lint-imports
.venv/bin/pytest
.venv/bin/python -m build
.venv/bin/python tools/check_distribution.py dist/*.whl dist/*.tar.gz
```

## Architecture

The stable facade coordinates three application services over provider-neutral
ports. Adapters implement those ports, while optional transports call only the
facade:

```text
domain <- ports <- application <- public facade
             ^          ^
             |          |
          adapters   optional integrations
```

Read the [full architecture and runtime-flow diagram](docs/architecture.md) and
[layered architecture ADR](docs/adr/0001-layered-architecture.md) before adding
package modules.

## Security and privacy

Memory content may contain sensitive personal information. The design requires
complete scope isolation, content-safe observability, untrusted model-output
validation, and explicit adapter capabilities. These contracts are not a
production security or compliance guarantee.

See the [security and privacy guide](docs/security-and-privacy.md) and
[SECURITY.md](SECURITY.md) for boundaries and reporting guidance.

## Limitations

The project does not supply production authentication, encryption, backup,
retention, rate limiting, provider governance, network MCP, infrastructure, or
operations. Store deletion does not prove erasure from backups, providers,
logs, or external systems. Exact vector search is a correctness-oriented
reference default and carries no workload guarantee.

See [compatibility](docs/compatibility.md), the [production guide](docs/production.md),
and the [FAQ](docs/faq.md) for the complete boundary.
