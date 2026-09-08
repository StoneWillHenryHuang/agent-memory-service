# Google Vertex AI Embeddings

`GoogleVertexEmbedder` is the optional provider reference implementation of the
provider-neutral `Embedder` port. It uses the public Google Gen AI SDK in Vertex
AI mode and is not imported by the base package or core layers.

## Installation

From a source checkout, install this provider independently:

```bash
python -m pip install ".[google-vertex]"
```

This extra does not install the PostgreSQL or OpenAI-compatible adapters.

## Explicit configuration

```python
from portable_memory_engine.adapters.google_vertex import (
    GoogleVertexEmbedder,
    GoogleVertexEmbeddingConfig,
)

config = GoogleVertexEmbeddingConfig(
    project="example-project",
    region="us-central1",
    model="text-embedding-model",
    dimension=768,
    batch_size=16,
)
embedder = GoogleVertexEmbedder(config)

await embedder.open()
try:
    embeddings = await embedder.embed(requests)
finally:
    await embedder.close()
```

Project, region, model, output dimension, and batch size all come from the
caller. The adapter has no environment-derived project, default region, model,
dimension, or gateway. If no credentials object is supplied, the Google SDK
resolves its normal Application Default Credentials when `open()` constructs
the client; callers may instead inject an explicit Google credentials object or
an already-created async SDK client.

The default batch size is one because provider model limits differ. Callers may
choose a value from 1 through 250 that is valid for their selected model. Input
order is preserved across batches. Automatic truncation is disabled by default
so oversized input does not silently change meaning.

## Task hints and result validation

The portable port exposes only document and query embedding purposes. This
adapter maps them at the provider boundary:

| Portable task | Vertex AI task hint |
|---|---|
| `DOCUMENT` | `RETRIEVAL_DOCUMENT` |
| `QUERY` | `RETRIEVAL_QUERY` |

The mapping is not present in the domain, ports, or application layers.

For every batch the adapter requests the configured output dimension and then
requires exactly one non-empty, finite vector of that dimension per input. An
empty response, a result-count mismatch, or a vector/dimension mismatch raises
`ProviderParseError`. No memory-store write can begin from such a result.
Returned vectors are copied into immutable tuples, so later mutation of a
provider response or cache cannot alter a domain embedding.

Provider 429 and 5xx responses and transport failures become the sanitized
`ProviderUnavailableError`. Other native API failures become `ProviderError`.
Public errors do not include input text, credentials, native response bodies, or
provider exception text.

## Lifecycle and ownership

Construction and import perform no network access or credential lookup. An
adapter-created client is opened explicitly and closed by the adapter after all
active calls finish. An injected client is caller-owned and is never closed by
the adapter. `open()` and `close()` are idempotent, and cancellation is not
converted into a provider error.

## Model and dimension changes

Embedding identity is the pair of model coordinate space and dimension. Do not
mix vectors produced by different model versions merely because their lengths
match: similarity scores are not guaranteed to be comparable.

Changing either the model or dimension therefore requires an explicit migration:

1. create a new vector column/store/index configured for the target dimension;
2. re-embed every searchable record with the target model;
3. rebuild and validate the vector index with only target-model vectors;
4. direct query embeddings to the same target model and dimension;
5. switch reads only after backfill is complete, then retire the old index under
   the deployment's retention policy.

The PostgreSQL adapter rejects a configured-dimension mismatch at `open()`.
Changing only the model still requires re-embedding and an index generation
change even when the dimension is unchanged. The SDK does not silently migrate,
project, pad, truncate, or combine incompatible vectors.

## Testing boundary

Adapter tests inject an async fake client and synthetic SDK responses. They do
not read credentials, contact Vertex AI, or require a Google Cloud account.
