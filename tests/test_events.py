import json

from app.events import (
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    serialize,
)


def _parse_frame(raw: bytes) -> tuple[str, dict, str | None]:
    """Parse one SSE frame. Returns (kind, data, event_id).

    event_id is the string from the ``id:`` line, or None if absent.
    """
    text = raw.decode("utf-8")
    assert text.endswith("\n\n"), "frame must terminate with blank line"
    body = text[:-2]
    lines = body.split("\n")
    assert 2 <= len(lines) <= 3, f"frame must be 2-3 lines, got {lines!r}"
    assert lines[0].startswith("event: ")
    kind = lines[0][len("event: "):]
    event_id: str | None = None
    data_str: str | None = None
    for line in lines[1:]:
        if line.startswith("id: "):
            event_id = line[len("id: "):]
        elif line.startswith("data: "):
            data_str = line[len("data: "):]
    assert data_str is not None, "frame missing data: line"
    return kind, json.loads(data_str), event_id


def test_text_event_roundtrip():
    raw = serialize(TextEvent(text="hello\nworld", message_ord=3))
    kind, data, _ = _parse_frame(raw)
    assert kind == "text"
    assert data == {"text": "hello\nworld", "message_ord": 3}


def test_tool_call_event_roundtrip():
    raw = serialize(ToolCallEvent(id="t1", name="echo", input={"text": "x"}, message_ord=4))
    kind, data, _ = _parse_frame(raw)
    assert kind == "tool_call"
    assert data == {"id": "t1", "name": "echo", "input": {"text": "x"}, "message_ord": 4}


def test_tool_result_event_roundtrip():
    raw = serialize(
        ToolResultEvent(tool_use_id="t1", output="result body", is_error=False, message_ord=5)
    )
    kind, data, _ = _parse_frame(raw)
    assert kind == "tool_result"
    assert data == {
        "tool_use_id": "t1",
        "output": "result body",
        "is_error": False,
        "message_ord": 5,
    }


def test_done_event_roundtrip():
    raw = serialize(DoneEvent(session_id="abc", usage={"input_tokens": 10}, is_error=False))
    kind, data, _ = _parse_frame(raw)
    assert kind == "done"
    assert data == {"session_id": "abc", "usage": {"input_tokens": 10}, "is_error": False}


def test_error_event_roundtrip():
    raw = serialize(ErrorEvent(message="boom"))
    kind, data, _ = _parse_frame(raw)
    assert kind == "error"
    assert data == {"message": "boom"}


def test_id_field_present_on_events_with_message_ord():
    cases = [
        (TextEvent(text="hi", message_ord=7), 7),
        (ToolCallEvent(id="t", name="echo", input={}, message_ord=12), 12),
        (ToolResultEvent(tool_use_id="t", output="x", is_error=False, message_ord=99), 99),
    ]
    for event, expected_ord in cases:
        _, _, event_id = _parse_frame(serialize(event))
        assert event_id == str(expected_ord), f"expected id={expected_ord} for {event!r}"


def test_no_id_on_events_without_message_ord():
    for event in (
        DoneEvent(session_id="abc", usage={}, is_error=False),
        ErrorEvent(message="boom"),
    ):
        raw = serialize(event).decode("utf-8")
        assert "id:" not in raw, f"unexpected id: line in {event!r}"


def test_framing_is_strict():
    raw = serialize(TextEvent(text="x", message_ord=0)).decode("utf-8")
    # id: is emitted for events that carry message_ord
    assert "\nid: 0\n" in raw
    assert "\nretry:" not in raw
    assert not raw.startswith(":")
    assert raw.count("\n\n") == 1


def test_data_is_single_line_json():
    raw = serialize(TextEvent(text="a\nb", message_ord=0)).decode("utf-8")
    body = raw[: -len("\n\n")]
    data_line = next(line for line in body.split("\n") if line.startswith("data: "))
    assert "\n" not in data_line[len("data: "):]
