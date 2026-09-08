# MCP Reference Integration

The optional MCP integration is a non-production example of exposing the stable
`portable_memory_engine.MemoryEngine` facade as three tools. It uses the
official MCP Python SDK 2.x and process stdio only.

## Install

From a source checkout, install the integration independently:

```bash
python -m pip install ".[mcp]"
```

The base SDK does not install or import MCP. The MCP extra is also independent
of the PostgreSQL, OpenAI-compatible, Google Vertex, and FastAPI extras.

## Run the synthetic server

From a source checkout:

```bash
python examples/mcp_reference.py
```

The example uses an in-memory store, deterministic embeddings, a fixed clock,
and a scripted model. It reads MCP messages from stdin and writes protocol
messages to stdout. It has no credentials, environment loading, database,
provider network request, or listening socket.

A local MCP host can configure the process without an environment or credential
field:

```json
{
  "mcpServers": {
    "portable-memory-reference": {
      "command": "python",
      "args": ["/absolute/path/to/examples/mcp_reference.py"]
    }
  }
}
```

The path is a caller-owned local filesystem path. The reference has no URL and
does not accept a credential in a query string, argument, or environment value.

## Exact capability and tools

The server advertises the tools capability only. It registers exactly:

| Tool | Behavior | Side effect |
|---|---|---|
| `add_memory` | Maps one strict input to `MemoryEngine.add` | Idempotent write |
| `recall_memory` | Maps one strict input to `MemoryEngine.recall` | Read only |
| `delete_memory` | Maps one discriminated input to `MemoryEngine.delete` | Idempotent destructive write |

It advertises no resources, prompts, logging, sampling, roots, or task
capability. The reference runner exposes no Streamable HTTP or SSE option.

The project owns each input and output JSON Schema. Unknown fields are rejected.
No schema contains an API key, authorization value, client identifier, client
registry, or source convention.

## Scope and context isolation

Every add and recall call requires a complete scope containing optional tenant,
required subject, and required namespace values. Memory-ID, scope, and session
deletion also require a complete scope; subject deletion requires an explicit
tenant-and-subject boundary. No boundary is inferred from MCP client metadata or
remembered from an earlier request.

Each protocol connection receives a new opaque context ID. Each tool call gets
a separate server-generated request ID. The server stores no messages, queries,
memory values, credentials, or caller identity in those IDs. A new connection
cannot reuse context state from an earlier one; durable memory visibility is
controlled only by the injected `MemoryEngine` store and the explicit scope.

## Authorization hook

The default `AllowLocalStdioAuthorization` is intentionally permissive because
the process is started and controlled by the local caller. It is not production
authentication. A caller can inject a hook at the factory boundary:

```python
from portable_memory_engine import MemoryScope
from portable_memory_engine.integrations.mcp import (
    McpAuthorizationContext,
    create_reference_mcp_server,
)


class ReferenceScopeAuthorization:
    async def authorize(self, context: McpAuthorizationContext) -> bool:
        boundary = context.boundary
        return isinstance(boundary, MemoryScope) and boundary.namespace == "synthetic-reference"


server = create_reference_mcp_server(
    engine=engine,
    authorization=ReferenceScopeAuthorization(),
)
```

The hook receives only the tool name, opaque request/context IDs, and the
explicit `MemoryScope` or `MemorySubject`. Its representation hides the memory
boundary. It receives no raw messages, semantic query, deletion identifier,
credential, request arguments, MCP client name, or transport header.

## Lifecycle

One stdio connection opens and closes the injected engine through the server
lifespan by default. Set `ReferenceMcpConfig(owns_engine=False)` only when the
surrounding process has already opened that exact engine and will close it after
the MCP connection ends.

The stdio runner is the supported entry point:

```python
import asyncio

from portable_memory_engine.integrations.mcp import run_reference_mcp_stdio

asyncio.run(run_reference_mcp_stdio(server))
```

## Results and errors

Successful calls return structured content matching the tool's output schema
plus a short generic text summary. Recall output intentionally contains the
requested memory content and public provenance session ID; it omits source and
client fields.

Validation, authorization, capability, conflict, provider, store, lifecycle,
and unexpected failures return `isError=true` with a bounded code, generic
message, and server-generated reference ID. They do not include request input,
message or memory content, semantic query, native exception text, provider
output, or authorization details. Cancellation remains cancellation rather than
being converted to an error result.

The integration sends no logging notification and emits no application log. It
also removes the MCP SDK's default telemetry middleware from this reference
server so tool arguments and results are not observed by that middleware.

## Production boundary

Do not turn this reference into a network service by changing only the
transport. A production MCP service requires a separate design for
authentication, per-subject and per-tenant authorization, transport security,
origin and proxy trust, limits, timeouts, rate limiting, secret management,
audit policy, retention, monitoring, incident response, and deployment.
