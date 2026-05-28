import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from app import db
from app.agent import _build_options, stream_turn
from app.config import Settings
from app.db import SessionRow


def _settings(model: str | None = None) -> Settings:
    return Settings(
        app_auth_token="t",
        anthropic_api_key="k",
        conversations_db_path=Path("/dev/null"),
        anthropic_model=model,
    )


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    await db.init_db(path)
    return path


def _assistant(*blocks: Any) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id=None,
        stop_reason=None,
        session_id=None,
        uuid=None,
    )


def _user_with_tool_result(
    tool_use_id: str, content: Any, is_error: bool = False
) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)
        ],
        uuid=None,
        parent_tool_use_id=None,
        tool_use_result=None,
    )


def _result(
    session_id: str,
    is_error: bool = False,
    usage: dict[str, Any] | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=80,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        stop_reason="end_turn",
        total_cost_usd=0.001,
        usage=usage or {"input_tokens": 10, "output_tokens": 5},
        result=None,
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        deferred_tool_use=None,
        errors=None,
        api_error_status=None,
        uuid=None,
    )


def _patch_query(
    monkeypatch: pytest.MonkeyPatch,
    messages: list[Any],
    capture: list[Any] | None = None,
) -> None:
    async def fake_query(
        *, prompt: str, options: Any = None
    ) -> AsyncIterator[Any]:
        if capture is not None:
            capture.append(SimpleNamespace(prompt=prompt, options=options))
        for m in messages:
            yield m

    monkeypatch.setattr("app.agent.query", fake_query)


# ----- options -----

def test_build_options_includes_mcp_server_and_allowed_tools() -> None:
    from app.tools import ALLOWED_TOOLS, MCP_SERVER

    options = _build_options(_settings(), sdk_session_id=None)
    assert options.mcp_servers == {"local": MCP_SERVER}
    assert options.allowed_tools == ALLOWED_TOOLS


def test_build_options_omits_resume_when_session_id_is_none() -> None:
    options = _build_options(_settings(), sdk_session_id=None)
    assert options.resume is None


def test_build_options_passes_resume_when_session_id_is_set() -> None:
    options = _build_options(_settings(), sdk_session_id="sdk-abc")
    assert options.resume == "sdk-abc"


def test_build_options_omits_model_when_none() -> None:
    options = _build_options(_settings(model=None), sdk_session_id=None)
    assert options.model is None


def test_build_options_passes_model_when_set() -> None:
    options = _build_options(
        _settings(model="claude-sonnet-4-6"), sdk_session_id=None
    )
    assert options.model == "claude-sonnet-4-6"


# ----- streaming -----

async def test_happy_path_yields_expected_events_and_persists_rows(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session: SessionRow = await db.create_session(db_path, title="t")

    messages = [
        _assistant(
            TextBlock(text="thinking..."),
            ToolUseBlock(id="t1", name="echo", input={"text": "x"}),
        ),
        _user_with_tool_result("t1", [{"type": "text", "text": "x"}], is_error=False),
        _assistant(TextBlock(text="all done")),
        _result(session_id="sdk-123"),
    ]
    _patch_query(monkeypatch, messages)

    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "TextEvent",
        "ToolCallEvent",
        "ToolResultEvent",
        "TextEvent",
        "DoneEvent",
    ]
    assert events[0].text == "thinking..."  # type: ignore[attr-defined]
    assert events[1].name == "echo"  # type: ignore[attr-defined]
    assert events[2].is_error is False  # type: ignore[attr-defined]
    assert events[2].output == "x"  # type: ignore[attr-defined]
    assert events[3].text == "all done"  # type: ignore[attr-defined]
    assert events[4].session_id == "sdk-123"  # type: ignore[attr-defined]

    rows = await db.list_messages(db_path, session.id)
    assert [r.kind for r in rows] == ["text", "tool_call", "tool_result", "text"]
    assert json.loads(rows[0].content_json) == {"text": "thinking..."}
    assert json.loads(rows[1].content_json) == {
        "id": "t1",
        "name": "echo",
        "input": {"text": "x"},
    }
    assert json.loads(rows[2].content_json) == {
        "tool_use_id": "t1",
        "output": "x",
        "is_error": False,
    }


async def test_first_turn_captures_sdk_session_id(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")
    assert session.sdk_session_id is None

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="ok")), _result(session_id="sdk-NEW")],
    )
    [_ async for _ in stream_turn(_settings(), db_path, session, "hi")]

    refreshed = await db.get_session(db_path, session.id)
    assert refreshed is not None
    assert refreshed.sdk_session_id == "sdk-NEW"


async def test_resume_uses_existing_sdk_session_id(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")
    await db.update_session_sdk_id(db_path, session.id, "sdk-EXISTING")
    refreshed = await db.get_session(db_path, session.id)
    assert refreshed is not None

    capture: list[Any] = []
    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="ok")), _result(session_id="sdk-EXISTING")],
        capture=capture,
    )
    [_ async for _ in stream_turn(_settings(), db_path, refreshed, "hi")]

    assert capture[0].options.resume == "sdk-EXISTING"


async def test_error_mid_stream_yields_error_event_and_no_done(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")

    async def fake_query(*, prompt: str, options: Any = None) -> AsyncIterator[Any]:
        yield _assistant(TextBlock(text="hello"))
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.agent.query", fake_query)

    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    kinds = [type(e).__name__ for e in events]
    assert kinds == ["TextEvent", "ErrorEvent"]
    assert "kaboom" in events[1].message  # type: ignore[attr-defined]

    rows = await db.list_messages(db_path, session.id)
    assert [r.kind for r in rows] == ["text", "error"]
    assert json.loads(rows[1].content_json) == {"message": "kaboom"}


async def test_done_event_carries_usage_and_session_id(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")

    _patch_query(
        monkeypatch,
        [
            _result(
                session_id="sdk-Z", usage={"input_tokens": 99, "output_tokens": 11}
            )
        ],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    assert len(events) == 1
    done = events[0]
    assert done.session_id == "sdk-Z"  # type: ignore[attr-defined]
    assert done.usage == {"input_tokens": 99, "output_tokens": 11}  # type: ignore[attr-defined]
    assert done.is_error is False  # type: ignore[attr-defined]


async def test_subsequent_turn_touches_session_without_overwriting_sdk_id(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")
    await db.update_session_sdk_id(db_path, session.id, "sdk-FIXED")
    refreshed = await db.get_session(db_path, session.id)
    assert refreshed is not None

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="ok")), _result(session_id="sdk-FIXED")],
    )
    [_ async for _ in stream_turn(_settings(), db_path, refreshed, "hi")]

    again = await db.get_session(db_path, session.id)
    assert again is not None
    assert again.sdk_session_id == "sdk-FIXED"
    assert again.updated_at >= refreshed.updated_at
