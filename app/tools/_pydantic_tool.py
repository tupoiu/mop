from typing import Any, Awaitable, Callable, Literal, TypeVar

from claude_agent_sdk import SdkMcpTool
from claude_agent_sdk import tool as sdk_tool
from pydantic import BaseModel

A = TypeVar("A", bound=BaseModel)


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolResult(BaseModel):
    content: list[TextContent]


def pydantic_tool(
    name: str,
    description: str,
    args_model: type[A],
) -> Callable[[Callable[[A], Awaitable[BaseModel]]], SdkMcpTool[Any]]:
    schema = args_model.model_json_schema()

    def decorator(
        handler: Callable[[A], Awaitable[BaseModel]],
    ) -> SdkMcpTool[Any]:
        @sdk_tool(name, description, schema)
        async def wrapped(raw_args: dict[str, Any]) -> dict[str, Any]:
            parsed = args_model.model_validate(raw_args)
            result = await handler(parsed)
            return result.model_dump()

        return wrapped

    return decorator
