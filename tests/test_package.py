from __future__ import annotations

import subprocess
import sys
from importlib.metadata import metadata, requires, version

import portable_memory_engine


def test_public_surface_is_intentionally_minimal() -> None:
    assert portable_memory_engine.__all__ == (
        "AccessDeniedError",
        "AddMemoryCommand",
        "AddMemoryResult",
        "AddResultStatus",
        "CapabilityError",
        "ChatModel",
        "ChatRequest",
        "ChatResponse",
        "ConflictError",
        "ConversationMessage",
        "DefaultPromptProvider",
        "DeleteCommand",
        "DeleteMemoryCommand",
        "DeleteResult",
        "DeleteScopeCommand",
        "DeleteSessionCommand",
        "DeleteSubjectCommand",
        "DeterministicEmbedder",
        "DomainValidationError",
        "Embedder",
        "Embedding",
        "EmbeddingRequest",
        "EmbeddingTask",
        "FixedClock",
        "IdempotencyConflictError",
        "InMemoryMemoryStore",
        "LifecycleError",
        "MemoryEngine",
        "MemoryEngineError",
        "MemoryKind",
        "MemoryMatch",
        "MemoryPage",
        "MemoryQuery",
        "MemoryRecord",
        "MemoryScope",
        "MemoryStore",
        "MemorySubject",
        "PromptProvider",
        "PromptRequest",
        "PromptResolutionError",
        "PromptTemplate",
        "ProviderError",
        "ProviderParseError",
        "ProviderUnavailableError",
        "RecallMode",
        "RecallRequest",
        "RecallResult",
        "ScriptedChatModel",
        "StaleEventError",
        "StoreError",
        "StoreUnavailableError",
        "__version__",
    )
    assert portable_memory_engine.__version__ == "0.1.0.dev0"
    assert version("portable-memory-engine") == portable_memory_engine.__version__


def test_distribution_has_no_base_dependencies() -> None:
    requirements = requires("portable-memory-engine") or []
    assert requirements
    assert all("extra ==" in requirement for requirement in requirements)


def test_metadata_has_no_undeclared_license() -> None:
    package_metadata = metadata("portable-memory-engine")
    assert package_metadata.get("License-Expression") is None
    assert package_metadata.get("License") is None


def test_import_is_quiet_in_a_fresh_interpreter() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; import portable_memory_engine as p; "
                "assert p.MemoryEngine; assert p.InMemoryMemoryStore; "
                "assert 'google.genai' not in sys.modules; "
                "assert 'sqlalchemy' not in sys.modules; "
                "assert 'httpx' not in sys.modules; "
                "assert 'fastapi' not in sys.modules; "
                "assert 'mcp' not in sys.modules; "
                "assert 'mcp_types' not in sys.modules; "
                "assert 'pydantic' not in sys.modules; "
                "assert 'uvicorn' not in sys.modules; "
                "assert 'httpx2' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
