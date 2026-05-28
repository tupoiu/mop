import json

from app.events import (
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    serialize,
)


def _parse_frame(raw: bytes) -> tuple[str, dict]:
    text = raw.decode("utf-8")
    assert text.endswith("\n\n"), "frame must terminate with blank line"
    body = text[:-2]
    lines = body.split("\n")
    assert len(lines) == 2, f"frame must be exactly two lines, got {lines!r}"
    assert lines[0].startswith("event: ")
    assert lines[1].startswith("data: ")
    kind = lines[0][len("event: ") :]
    data = json.loads(lines[1][len("data: ") :])
    return kind, data


def test_text_event_roundtrip():
    raw = serialize(TextEvent(text="hello\nworld", message_ord=3))
    kind, data = _parse_frame(raw)
    assert kind == "text"
    assert data == {"text": "hello\nworld", "message_ord": 3}


def test_tool_call_event_roundtrip():
    raw = serialize(ToolCallEvent(id="t1", name="echo", input={"text": "x"}, message_ord=4))
    kind, data = _parse_frame(raw)
    assert kind == "tool_call"
    assert data == {"id": "t1", "name": "echo", "input": {"text": "x"}, "message_ord": 4}


def test_tool_result_event_roundtrip():
    raw = serialize(
        ToolResultEvent(tool_use_id="t1", output="result body", is_error=False, message_ord=5)
    )
    kind, data = _parse_frame(raw)
    assert kind == "tool_result"
    assert data == {
        "tool_use_id": "t1",
        "output": "result body",
        "is_error": False,
        "message_ord": 5,
    }


def test_done_event_roundtrip():
    raw = serialize(DoneEvent(session_id="abc", usage={"input_tokens": 10}, is_error=False))
    kind, data = _parse_frame(raw)
    assert kind == "done"
    assert data == {"session_id": "abc", "usage": {"input_tokens": 10}, "is_error": False}


def test_error_event_roundtrip():
    raw = serialize(ErrorEvent(message="boom"))
    kind, data = _parse_frame(raw)
    assert kind == "error"
    assert data == {"message": "boom"}


def test_framing_is_strict():
    raw = serialize(TextEvent(text="x", message_ord=0)).decode("utf-8")
    assert "\nid:" not in raw
    assert "\nretry:" not in raw
    assert not raw.startswith(":")
    assert raw.count("\n\n") == 1


def test_data_is_single_line_json():
    raw = serialize(TextEvent(text="a\nb", message_ord=0)).decode("utf-8")
    body = raw[: -len("\n\n")]
    data_line = body.split("\n")[1]
    assert "\n" not in data_line[len("data: ") :]
