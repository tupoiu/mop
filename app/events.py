import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, Union


@dataclass(frozen=True)
class TextEvent:
    text: str
    message_ord: int
    kind: Literal["text"] = "text"


@dataclass(frozen=True)
class ToolCallEvent:
    id: str
    name: str
    input: dict[str, Any]
    message_ord: int
    kind: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True)
class ToolResultEvent:
    tool_use_id: str
    output: str
    is_error: bool
    message_ord: int
    kind: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class DoneEvent:
    session_id: str
    usage: dict[str, Any]
    is_error: bool
    kind: Literal["done"] = "done"


@dataclass(frozen=True)
class ErrorEvent:
    message: str
    kind: Literal["error"] = "error"


SSEEvent = Union[TextEvent, ToolCallEvent, ToolResultEvent, DoneEvent, ErrorEvent]


def serialize(event: SSEEvent) -> bytes:
    payload = {k: v for k, v in asdict(event).items() if k != "kind"}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.kind}\ndata: {data}\n\n".encode("utf-8")
