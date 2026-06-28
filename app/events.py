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


@dataclass(frozen=True)
class AllyMetricsEvent:
    agent_words: int
    user_words: int
    message_count: int
    uk_time: str
    kind: Literal["ally_metrics"] = "ally_metrics"


@dataclass(frozen=True)
class AllySummaryEvent:
    topic: str
    classification: str
    agent_words: int
    user_words: int
    message_count: int
    uk_time: str
    warning: bool
    kind: Literal["ally_summary"] = "ally_summary"


SSEEvent = Union[
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    DoneEvent,
    ErrorEvent,
    AllyMetricsEvent,
    AllySummaryEvent,
]


def serialize(event: SSEEvent) -> bytes:
    payload = {k: v for k, v in asdict(event).items() if k != "kind"}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    id_line = (
        f"id: {event.message_ord}\n"
        if isinstance(event, (TextEvent, ToolCallEvent, ToolResultEvent))
        else ""
    )
    return f"event: {event.kind}\n{id_line}data: {data}\n\n".encode("utf-8")
