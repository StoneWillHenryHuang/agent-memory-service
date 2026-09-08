"""Exercise the synthetic MCP example over a real stdio subprocess."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import TextIO

from mcp import ClientSession, StdioServerParameters, stdio_client


def test_mcp_reference_lists_and_calls_tools_over_stdio() -> None:
    root = Path(__file__).parents[2]
    example = root / "examples" / "mcp_reference.py"

    async def scenario(errors: TextIO) -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(example)],
            cwd=root,
        )
        async with (
            stdio_client(parameters, errlog=errors) as streams,
            ClientSession(*streams, read_timeout_seconds=5) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            recalled = await session.call_tool(
                "recall_memory",
                arguments={
                    "scope": {
                        "tenant_id": "synthetic-tenant",
                        "subject_id": "synthetic-subject",
                        "namespace": "mcp-reference",
                    }
                },
            )

        assert [tool.name for tool in listed.tools] == [
            "add_memory",
            "recall_memory",
            "delete_memory",
        ]
        assert not recalled.is_error
        assert recalled.structured_content["items"] == []

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        asyncio.run(asyncio.wait_for(scenario(errors), timeout=10))
        errors.seek(0)
        assert errors.read() == ""


def test_mcp_reference_document_has_no_credential_url_example() -> None:
    guide = (Path(__file__).parents[2] / "docs" / "mcp-reference.md").read_text(encoding="utf-8")
    lowered = guide.lower()

    for forbidden in ("?api_key=", "?apikey=", "?key=", "&api_key=", "&token="):
        assert forbidden not in lowered
