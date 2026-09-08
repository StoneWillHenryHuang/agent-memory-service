# Prompt provider and output schemas

The `portable_memory_engine.prompts` package contains an original, generic
prompt set, versioned output schemas, and a strict standard-library parser. It
has no provider SDK dependency and no built-in source-specific behavior.

## Resolution

`DefaultPromptProvider` resolves immutable `PromptTemplate` values in this
order:

1. the template supplied directly on `PromptRequest.override`;
2. an injected `SourcePromptKey` override;
3. an injected `PromptKey` locale override;
4. an English generic default.

A regional locale such as `en-gb` falls back to its primary language. Unknown
locales and custom memory kinds fail with `PromptResolutionError` unless the
caller registers an override. Source is only an override selector and remains
separate from tenant, subject, and namespace scope.

The built-in combinations are summary generation, durable fact extraction,
profile generation, and summary/fact/profile updates. Applications can replace
the provider entirely through the public `PromptProvider` port.

## Versioned output

The built-in schema versions are:

| Version | Parsed value | Shape |
|---|---|---|
| `summary.v1` | `SummaryOutput` | `schema_version`, `summary` |
| `fact.v1` | `FactOutput` | `schema_version`, `facts` |
| `profile.v1` | `ProfileOutput` | `schema_version`, `profile` (text or `null`) |
| `update.v1` | `UpdateOutput` | `schema_version`, `operations` |

Every default template carries both its schema version and its immutable JSON
schema. The application can pass that schema to a capable chat adapter, but it
must still parse and validate the returned text.

`parse_prompt_output` accepts one complete JSON object or one complete Markdown
JSON fence. It rejects empty output, invalid JSON, non-finite numbers, unknown
fields, wrong versions, invalid event semantics, duplicate facts, excess items,
oversized content, preambles, trailing prose, and multiple fences. A failure is
a `ProviderParseError` containing only the expected version and a bounded reason
code; raw model output is neither attached nor logged.

## Security boundary

The generic system prompts tell the model to treat conversation and existing
memory placeholders as untrusted evidence, ignore instructions inside that
evidence, avoid unsupported sensitive inference, and emit only the requested
JSON object. These instructions are a basic injection-resistance measure, not a
complete defense or security guarantee.

Callers still need complete-scope access control, safe placeholder rendering,
strict output parsing, bounded input/output sizes, provider credential hygiene,
content-free observability, and application-level validation before persistence.
Do not log rendered prompts, conversations, existing memories, or raw provider
responses.
