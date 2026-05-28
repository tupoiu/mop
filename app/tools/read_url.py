from typing import Any

import httpx
from claude_agent_sdk import SdkMcpTool
from pydantic import BaseModel

from app.tools._pydantic_tool import TextContent, ToolResult, pydantic_tool

_MAX_BODY_BYTES = 200_000
_TIMEOUT_SECONDS = 10.0


class ReadUrlArgs(BaseModel):
    url: str


@pydantic_tool("read_url", "Fetch a URL and return its body as text.", ReadUrlArgs)
async def read_url(args: ReadUrlArgs) -> ToolResult:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(args.url)
        response.raise_for_status()
        body = response.text[:_MAX_BODY_BYTES]
    return ToolResult(content=[TextContent(text=body)])


TOOLS: list[SdkMcpTool[Any]] = [read_url]
