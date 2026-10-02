# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2,<3"]
# ///
"""Calls the agents MCP server over stdio, the way Claude Code does. Run through ./check:
    ./check                      lists the tools
    ./check agents               calls a tool
    ./check send '{"agent": "agent-5", "prompt": "Reply with just OK."}'
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> int:
    params = StdioServerParameters(command=str(Path(__file__).with_name("serve")))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if len(sys.argv) < 2:
                for tool in (await session.list_tools()).tools:
                    print(f"{tool.name}: {(tool.description or '').splitlines()[0]}")
                return 0
            arguments = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
            result = await session.call_tool(sys.argv[1], arguments, read_timeout_seconds=700)
            structured = getattr(result, "structured_content", None)
            if structured is not None:
                print(json.dumps(structured, indent=2, ensure_ascii=False))
            else:
                for block in result.content:
                    print(getattr(block, "text", block))
            return 1 if getattr(result, "is_error", False) else 0


sys.exit(asyncio.run(main()))
