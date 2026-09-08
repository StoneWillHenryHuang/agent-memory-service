# Contributing

Portable Memory Engine is currently a pre-publication candidate. This document defines the technical contribution workflow; contribution acceptance and licensing terms must be finalized before public release.

## Independent contribution rule

Contributions must be original work or use clearly identified third-party
material with a compatible, documented license.

Use synthetic examples and test data. Never include credentials, personal data,
non-public domains, account identifiers, restricted issue links, or sensitive
deployment details.

## Development workflow

1. Use Python 3.12 or later.
2. Create an isolated virtual environment.
3. Install the development extra with `python -m pip install -e ".[dev]"`.
4. Keep dependencies pointing inward according to ADR 0001.
5. Add typed tests for every behavioral change.
6. Run all local quality checks listed in `README.md`.
7. Inspect built wheel and sdist contents before sharing an artifact.

## Design expectations

- Domain code uses the standard library only.
- Ports expose behavior, not provider, HTTP, ORM, SQL, or cloud types.
- Base imports perform no configuration loading, network access, or database initialization.
- Scope checks are part of store operations, not post-query filtering.
- Missing adapter capabilities fail explicitly.
- Logs and observer events exclude content and credentials by default.

Breaking public API, identity, concurrency, freshness, or deletion changes require an ADR update and migration note.

Documentation changes must keep local links valid, use only public documentation
URLs or synthetic localhost/example.com addresses, and avoid performance,
compliance, backend, or production-readiness claims not supported by tests. See
the [documentation map](docs/index.md) and
[migration policy](docs/migration-and-versioning.md).
