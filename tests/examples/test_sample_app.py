"""Verify the complete synthetic sample application."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from examples.sample_app import SYNTHETIC_MEMORY, SampleResult, run_sample

from portable_memory_engine import AddResultStatus


def test_sample_app_returns_the_documented_add_recall_delete_result() -> None:
    result = asyncio.run(run_sample())

    assert result == SampleResult(
        add_status=AddResultStatus.SUCCEEDED,
        recalled_contents=(SYNTHETIC_MEMORY,),
        deleted_count=1,
        remaining_count=0,
    )


def test_sample_app_runs_quietly_without_files_or_network(tmp_path: Path) -> None:
    example = Path(__file__).parents[2] / "examples" / "sample_app.py"
    result = subprocess.run(
        [sys.executable, "-I", str(example)],
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
