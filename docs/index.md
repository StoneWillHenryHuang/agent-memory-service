# Documentation

Portable Memory Engine is a pre-release, async-first Python library for scoped
conversational memory. This page routes readers to normative behavior,
implementation choices, and operational limitations.

## Start here

- [README](../README.md): problem, status, installation, and five-minute quickstart.
- [Synthetic sample app](../examples/sample_app.py): offline add, recall, and
  delete through the stable package-root facade.
- [Concepts](concepts.md): scope, provenance, memory kinds, identity, event time,
  optimistic concurrency, recall, and deletion.
- [Architecture](architecture.md): complete layer and runtime-flow diagram.
- [Compatibility and limitations](compatibility.md): implemented and excluded
  v0.1 behavior.
- [FAQ](faq.md): direct answers about scope, providers, deletion, and maturity.

## Build with the SDK

- [Public API](api-reference.md)
- [Exceptions](exceptions.md)
- [Extraction](extraction.md)
- [Recall](recall.md)
- [Deletion](deletion.md)
- [Prompts and strict output schemas](prompts.md)

## Select an implementation

- [Adapter and integration matrix](adapters.md)
- [Adapter contracts](adapter-contracts.md)
- [PostgreSQL + pgvector](postgresql.md)
- [OpenAI-compatible chat](openai-compatible.md)
- [Google Vertex embeddings](google-vertex-embeddings.md)
- [FastAPI localhost reference](fastapi-reference.md)
- [MCP stdio reference](mcp-reference.md)

## Operate and evolve

- [Production readiness gap](production.md)
- [Security and privacy](security-and-privacy.md)
- [Migration and versioning](migration-and-versioning.md)
- [Security reporting status](../SECURITY.md)
- [Contributing](../CONTRIBUTING.md)

## Decisions

Architecture decision records live in [`docs/adr`](adr/). They explain the
accepted boundaries for layering, scope, identity, concurrency, deletion,
capabilities, adapters, facade, and optional reference integrations.

The documentation describes only implemented behavior or explicit limitations.
It contains no production performance, compliance, backend-universality, or
hosted-service claim.
