# FastAPI Reference Service

The optional FastAPI app is a non-production example of wrapping the stable
`portable_memory_engine.MemoryEngine` facade. It keeps HTTP concepts out of the
domain and application layers.

## Install and run locally

From a source checkout, install only the reference integration when it is
needed:

```bash
python -m pip install ".[fastapi]"
```

From a source checkout, the synthetic app can be started with:

```bash
uvicorn examples.fastapi_reference:app \
  --host 127.0.0.1 --port 8000 --no-access-log
```

The example uses an in-memory store, deterministic embeddings, a fixed clock,
and a scripted model. It has no credentials, database, environment loading, or
network provider call. Its state disappears on restart and its script is sized
for the documented synthetic add flow only.

## Endpoint surface

The registered routes are exactly:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Content-free process readiness |
| `POST` | `/v1/memories/add` | Map an HTTP request to `MemoryEngine.add` |
| `POST` | `/v1/memories/recall` | Map an HTTP request to `MemoryEngine.recall` |
| `POST` | `/v1/memories/delete` | Map a discriminated delete request to `MemoryEngine.delete` |

The app disables served Swagger, ReDoc, and OpenAPI routes to keep that surface
exact. OpenAPI generation remains available in process:

```bash
python -c 'import json; from examples.fastapi_reference import app; print(json.dumps(app.openapi(), indent=2))' > openapi.json
```

The generated document declares only `http://127.0.0.1:8000` as its reference
server.

## Curl walkthrough

Check health:

```bash
curl --fail-with-body http://127.0.0.1:8000/health
```

Add one synthetic fact:

```bash
curl --fail-with-body \
  -H 'Content-Type: application/json' \
  -d '{
    "scope": {
      "tenant_id": "synthetic-tenant",
      "subject_id": "synthetic-traveler",
      "namespace": "reference"
    },
    "messages": [
      {"role": "user", "content": "I prefer morning trains for this synthetic trip."}
    ],
    "idempotency_key": "reference-add-1",
    "event_timestamp": "2026-01-01T00:00:00Z",
    "kinds": ["fact"],
    "source": "reference-guide",
    "session_id": "synthetic-session-1"
  }' \
  http://127.0.0.1:8000/v1/memories/add
```

Recall it:

```bash
curl --fail-with-body \
  -H 'Content-Type: application/json' \
  -d '{
    "scope": {
      "tenant_id": "synthetic-tenant",
      "subject_id": "synthetic-traveler",
      "namespace": "reference"
    },
    "kinds": ["fact"]
  }' \
  http://127.0.0.1:8000/v1/memories/recall
```

Delete the returned memory ID by replacing `synthetic-memory-id`:

```bash
curl --fail-with-body \
  -H 'Content-Type: application/json' \
  -d '{
    "target": "memory_id",
    "scope": {
      "tenant_id": "synthetic-tenant",
      "subject_id": "synthetic-traveler",
      "namespace": "reference"
    },
    "memory_id": "synthetic-memory-id",
    "idempotency_key": "reference-delete-1",
    "payload_digest": "reference-delete-digest-1",
    "event_timestamp": "2026-01-01T00:01:00Z"
  }' \
  http://127.0.0.1:8000/v1/memories/delete
```

Scope, subject, and session deletion use the same endpoint with `target` set to
`scope`, `subject`, or `session`. The generated OpenAPI document is the exact
transport contract.

## Transport and domain boundary

Pydantic models under `integrations.fastapi.schemas` validate only HTTP shape,
size limits, and discriminated request variants. The separate mapper constructs
the immutable public SDK commands and maps public results back to response
models. No domain model imports FastAPI or Pydantic, and the integration imports
no application internals.

The app lifespan owns the injected engine by default. Set
`ReferenceServiceConfig(owns_engine=False)` only when the surrounding process
already owns and opens that exact engine instance.

## Local security defaults

- The default authorization hook accepts only numeric loopback peer addresses;
  missing, hostname, and remote peers are denied.
- Allowed HTTP hosts default to `localhost`, `127.0.0.1`, and `[::1]`.
- CORS is denied by default. Explicit origins cannot contain wildcards,
  credentials, paths, queries, or fragments; credentials remain disabled.
- Every response receives a new server-generated `X-Request-ID`. A caller value
  is ignored rather than trusted or reflected.
- Error responses use a bounded envelope and never include request bodies,
  identifiers from validation failures, provider output, or native exceptions.
- The app emits no message, memory, semantic query, prompt, model response,
  metadata, embedding, authorization value, or API key log. The run command also
  disables access logs.

Example error shape:

```json
{
  "error": {
    "code": "invalid_http_request",
    "message": "request did not match the HTTP schema",
    "request_id": "server-generated-id"
  }
}
```

`AuthorizationHook` is intentionally small so an application can demonstrate
where an external decision belongs. It is not an API-key store, token verifier,
tenant registry, or production authorization implementation. The health route
is unprotected and returns no adapter, tenant, model, or storage details.

## Reference Dockerfile

Build the original minimal image from the repository root:

```bash
docker build -f examples/fastapi.Dockerfile -t portable-memory-reference .
docker run --rm portable-memory-reference
```

The image installs only the `fastapi` extra, runs as the non-root `memory-app`
user, disables access logs, and binds inside the container to loopback. It is a
packaging example, not a Kubernetes, cloud, reverse-proxy, TLS, or public-network
deployment template.

## Production boundary

Do not expose this app to an untrusted network. A production service must define
authentication, subject/tenant authorization, TLS and proxy trust, request size
limits, rate limiting, timeouts, secret management, audit policy, encryption,
retention, backups, provider governance, monitoring, and deployment hardening.
Adding an authorization hook alone does not turn this reference into that
service.
