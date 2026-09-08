# ADR 0012: Tools-only MCP reference over stdio and the public facade

- Status: Accepted
- Date: 2026-07-31

## Context

Applications may want a concrete Model Context Protocol example without making
MCP part of the SDK core or implying a hosted agent service. The reference must
not inherit deployment-specific client/source conventions, transport credentials, network
authentication, or state shared across tool connections.

## Decision

Provide an independent `mcp` extra using the current stable MCP Python SDK 2.x.
Build on the low-level `Server` so the advertised capability is tools only and
the project owns the exact JSON Schemas and result envelopes. Expose exactly
`add_memory`, `recall_memory`, and `delete_memory`, all delegating to the stable
package-root `MemoryEngine` facade.

Support process stdio only. Do not configure or document Streamable HTTP, SSE,
OAuth, API keys, resources, prompts, sampling, roots, tasks, or logging
notifications. Remove the SDK's default telemetry middleware from the reference
server. Catch validation, authorization, domain, provider, store, and unexpected
errors inside the tool handler and return bounded messages without native
exception text or input values.

Require every call to carry a complete scope or explicit subject boundary.
Create a new opaque context ID for each connection and a new request ID for each
tool call. Pass only the tool name, those opaque IDs, and the explicit memory
boundary to an injected authorization hook. Do not interpret MCP client metadata
or add a client/source registry.

## Consequences

- The base wheel and root import remain free of MCP and Pydantic dependencies.
- The optional extra carries the MCP SDK's own dependencies but does not pull a
  storage or model provider extra.
- Tool schemas and Python domain contracts remain separate and independently
  versioned.
- The synthetic example works without credentials, provider network, database,
  environment loading, or a URL.
- A production or network MCP service requires a separate security and
  operations design; changing transport is not a supported configuration switch.
