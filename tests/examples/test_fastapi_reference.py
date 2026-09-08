"""Smoke test the synthetic FastAPI app without opening a network socket."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_fastapi_reference_imports_quietly() -> None:
    example = Path(__file__).parents[2] / "examples" / "fastapi_reference.py"
    result = subprocess.run(
        [sys.executable, str(example)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
