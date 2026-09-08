"""Verify that built distributions contain only intended public files."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".open_source_extraction",
        "__pycache__",
        "tests",
    }
)
FORBIDDEN_NAMES = frozenset({"EXTRACTION_STATUS.md"})


def _wheel_names(path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(path) as archive:
        return tuple(archive.namelist())


def _sdist_names(path: Path) -> tuple[str, ...]:
    with tarfile.open(path, mode="r:gz") as archive:
        return tuple(archive.getnames())


def _relative_parts(name: str, *, sdist: bool) -> tuple[str, ...]:
    parts = PurePosixPath(name).parts
    return parts[1:] if sdist and parts else parts


def _check_forbidden(names: tuple[str, ...], *, sdist: bool) -> None:
    violations: list[str] = []
    for name in names:
        parts = _relative_parts(name, sdist=sdist)
        if FORBIDDEN_PARTS.intersection(parts) or FORBIDDEN_NAMES.intersection(parts):
            violations.append(name)
    if violations:
        joined = "\n".join(sorted(violations))
        raise SystemExit(f"distribution contains forbidden files:\n{joined}")


def _check_wheel(path: Path) -> None:
    names = _wheel_names(path)
    _check_forbidden(names, sdist=False)

    expected = {
        "portable_memory_engine/__init__.py",
        "portable_memory_engine/adapters/__init__.py",
        "portable_memory_engine/adapters/memory.py",
        "portable_memory_engine/adapters/google_vertex/__init__.py",
        "portable_memory_engine/adapters/google_vertex/config.py",
        "portable_memory_engine/adapters/google_vertex/embedder.py",
        "portable_memory_engine/adapters/openai_compatible/__init__.py",
        "portable_memory_engine/adapters/openai_compatible/config.py",
        "portable_memory_engine/adapters/openai_compatible/model.py",
        "portable_memory_engine/adapters/postgres/__init__.py",
        "portable_memory_engine/adapters/postgres/config.py",
        "portable_memory_engine/adapters/postgres/mapper.py",
        "portable_memory_engine/adapters/postgres/migrations.py",
        "portable_memory_engine/adapters/postgres/models.py",
        "portable_memory_engine/adapters/postgres/store.py",
        "portable_memory_engine/adapters/postgres/migration_history/env.py",
        "portable_memory_engine/adapters/postgres/migration_history/versions/0001_initial.py",
        "portable_memory_engine/adapters/testing.py",
        "portable_memory_engine/application/__init__.py",
        "portable_memory_engine/application/deletion.py",
        "portable_memory_engine/application/engine.py",
        "portable_memory_engine/application/extraction.py",
        "portable_memory_engine/application/freshness.py",
        "portable_memory_engine/application/identity.py",
        "portable_memory_engine/application/lifecycle.py",
        "portable_memory_engine/application/messages.py",
        "portable_memory_engine/application/model.py",
        "portable_memory_engine/application/models.py",
        "portable_memory_engine/application/orchestration.py",
        "portable_memory_engine/application/recall.py",
        "portable_memory_engine/application/rerank.py",
        "portable_memory_engine/application/selection.py",
        "portable_memory_engine/application/support.py",
        "portable_memory_engine/application/update.py",
        "portable_memory_engine/domain/__init__.py",
        "portable_memory_engine/domain/_validation.py",
        "portable_memory_engine/domain/enums.py",
        "portable_memory_engine/domain/errors.py",
        "portable_memory_engine/domain/models.py",
        "portable_memory_engine/domain/queries.py",
        "portable_memory_engine/domain/types.py",
        "portable_memory_engine/facade.py",
        "portable_memory_engine/integrations/__init__.py",
        "portable_memory_engine/integrations/fastapi/__init__.py",
        "portable_memory_engine/integrations/fastapi/app.py",
        "portable_memory_engine/integrations/fastapi/config.py",
        "portable_memory_engine/integrations/fastapi/mappers.py",
        "portable_memory_engine/integrations/fastapi/schemas.py",
        "portable_memory_engine/integrations/fastapi/security.py",
        "portable_memory_engine/integrations/mcp/__init__.py",
        "portable_memory_engine/integrations/mcp/config.py",
        "portable_memory_engine/integrations/mcp/mappers.py",
        "portable_memory_engine/integrations/mcp/schemas.py",
        "portable_memory_engine/integrations/mcp/security.py",
        "portable_memory_engine/integrations/mcp/server.py",
        "portable_memory_engine/ports/__init__.py",
        "portable_memory_engine/ports/lifecycle.py",
        "portable_memory_engine/ports/observability.py",
        "portable_memory_engine/ports/policy.py",
        "portable_memory_engine/ports/providers.py",
        "portable_memory_engine/ports/storage.py",
        "portable_memory_engine/ports/system.py",
        "portable_memory_engine/prompts/__init__.py",
        "portable_memory_engine/prompts/defaults.py",
        "portable_memory_engine/prompts/parser.py",
        "portable_memory_engine/prompts/schemas.py",
        "portable_memory_engine/py.typed",
        "portable_memory_engine/testing/__init__.py",
        "portable_memory_engine/testing/contracts/__init__.py",
        "portable_memory_engine/testing/contracts/memory_store.py",
    }
    missing = expected.difference(names)
    if missing:
        raise SystemExit(f"wheel is missing expected files: {sorted(missing)}")

    metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
    if metadata_name is None:
        raise SystemExit("wheel has no METADATA file")

    with zipfile.ZipFile(path) as archive:
        package_metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    requirements = package_metadata.get_all("Requires-Dist", [])
    unguarded = [value for value in requirements if "extra ==" not in value]
    if unguarded:
        raise SystemExit(f"wheel declares base dependencies: {unguarded}")
    extras = set(package_metadata.get_all("Provides-Extra", []))
    if not {
        "contract-tests",
        "dev",
        "fastapi",
        "google-vertex",
        "mcp",
        "openai-compatible",
        "postgres",
    }.issubset(extras):
        raise SystemExit("wheel is missing its declared extras")


def _check_sdist(path: Path) -> None:
    names = _sdist_names(path)
    _check_forbidden(names, sdist=True)
    required_suffixes = {
        ".dockerignore",
        "README.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "compose.postgres.yml",
        "docs/adapter-contracts.md",
        "docs/adapters.md",
        "docs/api-reference.md",
        "docs/architecture.md",
        "docs/compatibility.md",
        "docs/concepts.md",
        "docs/adr/0009-google-vertex-embeddings.md",
        "docs/adr/0010-public-facade.md",
        "docs/adr/0011-fastapi-reference-service.md",
        "docs/adr/0012-stdio-mcp-reference.md",
        "docs/deletion.md",
        "docs/exceptions.md",
        "docs/extraction.md",
        "docs/fastapi-reference.md",
        "docs/faq.md",
        "docs/google-vertex-embeddings.md",
        "docs/index.md",
        "docs/mcp-reference.md",
        "docs/migration-and-versioning.md",
        "docs/openai-compatible.md",
        "docs/prompts.md",
        "docs/production.md",
        "docs/product-scope.md",
        "docs/postgresql.md",
        "docs/recall.md",
        "docs/security-and-privacy.md",
        "examples/deletion_quickstart.py",
        "examples/extraction_quickstart.py",
        "examples/fastapi.Dockerfile",
        "examples/fastapi_reference.py",
        "examples/in_memory_quickstart.py",
        "examples/mcp_reference.py",
        "examples/prompt_parsing.py",
        "examples/recall_quickstart.py",
        "examples/sample_app.py",
        "pyproject.toml",
        "src/portable_memory_engine/__init__.py",
        "src/portable_memory_engine/adapters/__init__.py",
        "src/portable_memory_engine/adapters/memory.py",
        "src/portable_memory_engine/adapters/google_vertex/__init__.py",
        "src/portable_memory_engine/adapters/google_vertex/config.py",
        "src/portable_memory_engine/adapters/google_vertex/embedder.py",
        "src/portable_memory_engine/adapters/openai_compatible/__init__.py",
        "src/portable_memory_engine/adapters/openai_compatible/config.py",
        "src/portable_memory_engine/adapters/openai_compatible/model.py",
        "src/portable_memory_engine/adapters/postgres/__init__.py",
        "src/portable_memory_engine/adapters/postgres/config.py",
        "src/portable_memory_engine/adapters/postgres/mapper.py",
        "src/portable_memory_engine/adapters/postgres/migrations.py",
        "src/portable_memory_engine/adapters/postgres/models.py",
        "src/portable_memory_engine/adapters/postgres/store.py",
        "src/portable_memory_engine/adapters/postgres/migration_history/env.py",
        "src/portable_memory_engine/adapters/postgres/migration_history/versions/0001_initial.py",
        "src/portable_memory_engine/adapters/testing.py",
        "src/portable_memory_engine/application/__init__.py",
        "src/portable_memory_engine/application/deletion.py",
        "src/portable_memory_engine/application/engine.py",
        "src/portable_memory_engine/application/extraction.py",
        "src/portable_memory_engine/application/freshness.py",
        "src/portable_memory_engine/application/identity.py",
        "src/portable_memory_engine/application/lifecycle.py",
        "src/portable_memory_engine/application/messages.py",
        "src/portable_memory_engine/application/model.py",
        "src/portable_memory_engine/application/models.py",
        "src/portable_memory_engine/application/orchestration.py",
        "src/portable_memory_engine/application/recall.py",
        "src/portable_memory_engine/application/rerank.py",
        "src/portable_memory_engine/application/selection.py",
        "src/portable_memory_engine/application/support.py",
        "src/portable_memory_engine/application/update.py",
        "src/portable_memory_engine/domain/__init__.py",
        "src/portable_memory_engine/domain/_validation.py",
        "src/portable_memory_engine/domain/enums.py",
        "src/portable_memory_engine/domain/errors.py",
        "src/portable_memory_engine/domain/models.py",
        "src/portable_memory_engine/domain/queries.py",
        "src/portable_memory_engine/domain/types.py",
        "src/portable_memory_engine/facade.py",
        "src/portable_memory_engine/integrations/__init__.py",
        "src/portable_memory_engine/integrations/fastapi/__init__.py",
        "src/portable_memory_engine/integrations/fastapi/app.py",
        "src/portable_memory_engine/integrations/fastapi/config.py",
        "src/portable_memory_engine/integrations/fastapi/mappers.py",
        "src/portable_memory_engine/integrations/fastapi/schemas.py",
        "src/portable_memory_engine/integrations/fastapi/security.py",
        "src/portable_memory_engine/integrations/mcp/__init__.py",
        "src/portable_memory_engine/integrations/mcp/config.py",
        "src/portable_memory_engine/integrations/mcp/mappers.py",
        "src/portable_memory_engine/integrations/mcp/schemas.py",
        "src/portable_memory_engine/integrations/mcp/security.py",
        "src/portable_memory_engine/integrations/mcp/server.py",
        "src/portable_memory_engine/ports/__init__.py",
        "src/portable_memory_engine/ports/lifecycle.py",
        "src/portable_memory_engine/ports/observability.py",
        "src/portable_memory_engine/ports/policy.py",
        "src/portable_memory_engine/ports/providers.py",
        "src/portable_memory_engine/ports/storage.py",
        "src/portable_memory_engine/ports/system.py",
        "src/portable_memory_engine/prompts/__init__.py",
        "src/portable_memory_engine/prompts/defaults.py",
        "src/portable_memory_engine/prompts/parser.py",
        "src/portable_memory_engine/prompts/schemas.py",
        "src/portable_memory_engine/py.typed",
        "src/portable_memory_engine/testing/__init__.py",
        "src/portable_memory_engine/testing/contracts/__init__.py",
        "src/portable_memory_engine/testing/contracts/memory_store.py",
    }
    relative_names = {"/".join(_relative_parts(name, sdist=True)) for name in names}
    missing = required_suffixes.difference(relative_names)
    if missing:
        raise SystemExit(f"sdist is missing expected files: {sorted(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    args = parser.parse_args()

    _check_wheel(args.wheel)
    _check_sdist(args.sdist)
    print("distribution-check: clean")


if __name__ == "__main__":
    main()
