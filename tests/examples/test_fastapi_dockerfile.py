"""Check the reference container stays minimal, non-root, and loopback-only."""

from __future__ import annotations

from pathlib import Path


def test_fastapi_reference_dockerfile_has_safe_reference_defaults() -> None:
    dockerfile = Path(__file__).parents[2] / "examples" / "fastapi.Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    assert 'python -m pip install --no-cache-dir ".[fastapi]"' in text
    assert "USER memory-app" in text
    assert text.index("USER memory-app") < text.index('CMD ["uvicorn"')
    assert '"--host", "127.0.0.1"' in text
    assert '"--no-access-log"' in text
    for excluded in ("postgres", "google-vertex", "openai-compatible", "redis"):
        assert excluded not in text.lower()
