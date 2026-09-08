"""Execute the exact Python block from the README five-minute quickstart."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def test_readme_quickstart_is_copyable_and_network_free(tmp_path: Path) -> None:
    readme = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Five-minute in-memory quickstart", maxsplit=1)[1]
    section = section.split("\n## ", maxsplit=1)[0]
    match = re.search(r"```python\n(?P<code>.*?)\n```", section, flags=re.DOTALL)
    assert match is not None

    result = subprocess.run(
        [sys.executable, "-I", "-"],
        input=match.group("code"),
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
