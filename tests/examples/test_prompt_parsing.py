"""Smoke test for the network-free prompt parsing example."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_prompt_parsing_example_runs_quietly() -> None:
    root = Path(__file__).parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "examples" / "prompt_parsing.py")],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
