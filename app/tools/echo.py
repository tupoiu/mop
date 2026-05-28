from typing import Any

from claude_agent_sdk import SdkMcpTool
from pydantic import BaseModel

from app.tools._pydantic_tool import TextContent, ToolResult, pydantic_tool


class EchoArgs(BaseModel):
    text: str


@pydantic_tool("echo", "Return the input string unchanged.", EchoArgs)
async def echo(args: EchoArgs) -> ToolResult:
    return ToolResult(content=[TextContent(text=args.text)])


TOOLS: list[SdkMcpTool[Any]] = [echo]
