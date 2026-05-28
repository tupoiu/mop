from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool, tool

_MAX_BODY_BYTES = 200_000
_TIMEOUT_SECONDS = 10.0


@tool("read_url", "Fetch a URL and return its body as text.", {"url": str})
async def read_url(args: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        response = await client.get(args["url"])
        response.raise_for_status()
        body = response.text[:_MAX_BODY_BYTES]
    return {"content": [{"type": "text", "text": body}]}


TOOLS: list[SdkMcpTool[Any]] = [read_url]
