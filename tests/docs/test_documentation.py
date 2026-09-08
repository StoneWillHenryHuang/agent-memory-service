"""Check the public documentation narrative, links, URLs, and offline commands."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).parents[2]
LINK_PATTERN = re.compile(r"!?(?:\[[^\]]*\])\((?P<target>[^)]+)\)")
URL_PATTERN = re.compile(r"https?://[^\s<>`)\"\]]+")
EXAMPLE_COMMAND_PATTERN = re.compile(r"^python examples/(?P<name>[a-z0-9_]+\.py)$", re.MULTILINE)
PUBLIC_DOCUMENTATION_HOSTS = frozenset(
    {
        "cloud.google.com",
        "docs.python.org",
        "fastapi.tiangolo.com",
        "github.com",
        "modelcontextprotocol.io",
        "packaging.python.org",
        "platform.openai.com",
        "postgresql.org",
        "pypi.org",
        "py.sdk.modelcontextprotocol.io",
        "starlette.io",
    }
)
REQUIRED_DOCS = frozenset(
    {
        "adapters.md",
        "architecture.md",
        "compatibility.md",
        "concepts.md",
        "faq.md",
        "index.md",
        "migration-and-versioning.md",
        "production.md",
        "security-and-privacy.md",
    }
)
OFFLINE_EXAMPLES = (
    "in_memory_quickstart.py",
    "prompt_parsing.py",
    "extraction_quickstart.py",
    "recall_quickstart.py",
    "deletion_quickstart.py",
    "sample_app.py",
)


def _markdown_files() -> tuple[Path, ...]:
    top_level = (ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md")
    return (*top_level, *sorted((ROOT / "docs").rglob("*.md")))


def test_required_public_narrative_and_new_architecture_diagram_exist() -> None:
    docs = ROOT / "docs"
    assert REQUIRED_DOCS.issubset(path.name for path in docs.iterdir())

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in (
        "## Why this project",
        "## Project status",
        "## Capabilities",
        "## Five-minute in-memory quickstart",
        "## Architecture",
        "## Limitations",
    ):
        assert heading in readme

    architecture = (docs / "architecture.md").read_text(encoding="utf-8")
    assert "```mermaid" in architecture
    for node in (
        "MemoryEngine facade",
        "Add and extract",
        "Recall",
        "Delete",
        "PostgreSQL plus pgvector",
        "Optional MCP stdio reference",
    ):
        assert node in architecture


def test_every_markdown_link_resolves_or_uses_an_approved_public_host() -> None:
    for document in _markdown_files():
        content = document.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(content):
            target = match.group("target").strip().strip("<>")
            if target.startswith(("http://", "https://")):
                continue
            if target.startswith("#"):
                continue
            relative = unquote(target.split("#", maxsplit=1)[0])
            assert relative, f"empty link target in {document.relative_to(ROOT)}"
            resolved = (document.parent / relative).resolve()
            assert resolved.is_relative_to(ROOT)
            assert resolved.exists(), f"broken link in {document.relative_to(ROOT)}: {target}"

        for url in URL_PATTERN.findall(content):
            parsed = urlsplit(url.rstrip(".,;:"))
            host = parsed.hostname
            assert host is not None
            approved = (
                host in {"127.0.0.1", "localhost", "example.com"}
                or host.endswith(".example.com")
                or host in PUBLIC_DOCUMENTATION_HOSTS
            )
            assert approved, f"unapproved URL host in {document.relative_to(ROOT)}: {host}"
            if parsed.query:
                lowered_query = parsed.query.lower()
                for forbidden in ("api_key=", "apikey=", "access_token=", "token="):
                    assert forbidden not in lowered_query


def test_documented_python_example_commands_point_to_real_files() -> None:
    commands: set[str] = set()
    for document in _markdown_files():
        content = document.read_text(encoding="utf-8")
        commands.update(EXAMPLE_COMMAND_PATTERN.findall(content))

    assert "sample_app.py" in commands
    for name in commands:
        assert (ROOT / "examples" / name).is_file()


@pytest.mark.parametrize("name", OFFLINE_EXAMPLES)
def test_documented_offline_examples_run_cleanly(name: str, tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / name)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert tuple(tmp_path.iterdir()) == ()


def test_docs_use_no_generated_or_unreviewed_visual_assets() -> None:
    forbidden_suffixes = {".gif", ".html", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
    assets = tuple(
        path.relative_to(ROOT)
        for path in (ROOT / "docs").rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden_suffixes
    )
    assert assets == ()


def test_pre_release_install_commands_do_not_assume_a_published_package() -> None:
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in _markdown_files())
    assert 'pip install "portable-memory-engine' not in rendered
    assert "pip install portable-memory-engine" not in rendered
