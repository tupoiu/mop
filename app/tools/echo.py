from typing import Any

from claude_agent_sdk import SdkMcpTool, tool


@tool("echo", "Return the input string unchanged.", {"text": str})
async def echo(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": args["text"]}]}


TOOLS: list[SdkMcpTool[Any]] = [echo]
