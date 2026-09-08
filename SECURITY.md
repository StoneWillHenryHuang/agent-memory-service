# Security Policy

## Current status

Portable Memory Engine is a pre-release candidate and has no supported production version.

| Version | Supported |
|---|---|
| `0.1.0.dev0` | Design and development only |

## Reporting a vulnerability

Do not include secrets, credentials, personal data, memory content, prompts,
model responses, or sensitive deployment details in an issue or reproduction.

Use the repository issue tracker to report a security concern with only the
minimum non-sensitive reproduction details. Request a private follow-up when
additional sensitive context is necessary.

## Scope reminders

The SDK does not provide production authentication, encryption, backup controls, provider-retention guarantees, or regulatory compliance by itself. Deployments remain responsible for those controls and for reviewing the behavior of custom adapters and observers.

The optional FastAPI app is a localhost reference wrapper, not a security
boundary. Its loopback-only default hook, trusted-host list, empty CORS
allowlist, generic error envelopes, and content-free logging policy are defense
in depth for local examples; they are not production authentication or
authorization. Do not expose the reference app to an untrusted network.

The optional MCP integration is a local stdio reference, not a production agent
security boundary. It accepts no transport credential, URL, query string, or
client/source convention. Every tool requires an explicit memory boundary and
passes a content-safe request/context value to an injected authorization hook.
Do not adapt it to a network transport without a separate authentication,
authorization, proxy, origin, rate-limit, and deployment review.

The complete data-surface, isolation, model-output, secrets, observability, and
deletion boundaries are documented in the
[security and privacy guide](docs/security-and-privacy.md). The
[production guide](docs/production.md) lists controls that remain the deploying
application's responsibility.
