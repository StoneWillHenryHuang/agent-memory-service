"""Smoke test the documented network-free quickstart."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_in_memory_quickstart_runs_quietly() -> None:
    example = Path(__file__).parents[2] / "examples" / "in_memory_quickstart.py"
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
