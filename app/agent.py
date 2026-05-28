import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, TypedDict

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from app import db, tools
from app.config import Settings
from app.db import SessionRow
from app.events import (
    DoneEvent,
    ErrorEvent,
    SSEEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)

logger = logging.getLogger(__name__)


class _ToolCallPayload(TypedDict):
    id: str
    name: str
    input: dict[str, Any]


class _ToolResultPayload(TypedDict):
    tool_use_id: str
    output: str
    is_error: bool


def _build_options(settings: Settings, sdk_session_id: str | None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        mcp_servers={"local": tools.MCP_SERVER},
        allowed_tools=tools.ALLOWED_TOOLS,
        resume=sdk_session_id,
        model=settings.anthropic_model,
        include_partial_messages=True,
    )


def _format_tool_result(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text", "")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


async def stream_turn(
    settings: Settings,
    db_path: Path,
    session: SessionRow,
    user_content: str,
) -> AsyncIterator[SSEEvent]:
    options = _build_options(settings, session.sdk_session_id)
    sdk_session_id_persisted = session.sdk_session_id is not None

    streamed_any_text = False

    try:
        async for message in query(prompt=user_content, options=options):
            if isinstance(message, StreamEvent):
                raw = message.event
                if (
                    raw.get("type") == "content_block_delta"
                    and raw.get("delta", {}).get("type") == "text_delta"
                ):
                    streamed_any_text = True
                    yield TextEvent(text=raw["delta"]["text"], message_ord=0)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        row = await db.append_message(
                            db_path,
                            session_id=session.id,
                            role="assistant",
                            kind="text",
                            content_json=json.dumps({"text": block.text}),
                        )
                        # Only emit if text wasn't already streamed via deltas.
                        if not streamed_any_text:
                            yield TextEvent(text=block.text, message_ord=row.ord)
                    elif isinstance(block, ToolUseBlock):
                        payload: _ToolCallPayload = {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                        row = await db.append_message(
                            db_path,
                            session_id=session.id,
                            role="assistant",
                            kind="tool_call",
                            content_json=json.dumps(payload),
                        )
                        yield ToolCallEvent(
                            id=block.id,
                            name=block.name,
                            input=block.input,
                            message_ord=row.ord,
                        )
            elif isinstance(message, UserMessage):
                content = message.content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, ToolResultBlock):
                            output = _format_tool_result(block.content)
                            is_error = bool(block.is_error)
                            tr_payload: _ToolResultPayload = {
                                "tool_use_id": block.tool_use_id,
                                "output": output,
                                "is_error": is_error,
                            }
                            row = await db.append_message(
                                db_path,
                                session_id=session.id,
                                role="user",
                                kind="tool_result",
                                content_json=json.dumps(tr_payload),
                            )
                            yield ToolResultEvent(
                                tool_use_id=block.tool_use_id,
                                output=output,
                                is_error=is_error,
                                message_ord=row.ord,
                            )
            elif isinstance(message, ResultMessage):
                if not sdk_session_id_persisted:
                    await db.update_session_sdk_id(db_path, session.id, message.session_id)
                    sdk_session_id_persisted = True
                else:
                    await db.touch_session(db_path, session.id)
                yield DoneEvent(
                    session_id=message.session_id,
                    usage=message.usage or {},
                    is_error=message.is_error,
                )
                return
    except Exception as exc:
        logger.exception("agent stream failed for session %s", session.id)
        await db.append_message(
            db_path,
            session_id=session.id,
            role="assistant",
            kind="error",
            content_json=json.dumps({"message": str(exc)}),
        )
        yield ErrorEvent(message=str(exc))
        return
