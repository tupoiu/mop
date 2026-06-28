import asyncio
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
from app.ally import AllyAnalysis
from app.config import Settings
from app.db import SessionRow


@pytest.fixture(autouse=True)
def _stub_analyze(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: make the side-model deterministic and instant for every turn.

    Individual tests may re-monkeypatch ``app.ally.analyze`` to override.
    """

    async def fake_analyze(settings: Any, messages: Any) -> AllyAnalysis:
        return AllyAnalysis(topic="Test topic", classification="Other")

    monkeypatch.setattr("app.ally.analyze", fake_analyze)


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


def _user_with_tool_result(tool_use_id: str, content: Any, is_error: bool = False) -> UserMessage:
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)],
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
    async def fake_query(*, prompt: str, options: Any = None) -> AsyncIterator[Any]:
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
    assert options.allowed_tools == [*ALLOWED_TOOLS, "WebSearch"]


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
    options = _build_options(_settings(model="claude-sonnet-4-6"), sdk_session_id=None)
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
        "AllyMetricsEvent",
        "TextEvent",
        "ToolCallEvent",
        "ToolResultEvent",
        "TextEvent",
        "AllySummaryEvent",
        "DoneEvent",
    ]
    assert events[1].text == "thinking..."  # type: ignore[attr-defined]
    assert events[2].name == "echo"  # type: ignore[attr-defined]
    assert events[3].is_error is False  # type: ignore[attr-defined]
    assert events[3].output == "x"  # type: ignore[attr-defined]
    assert events[4].text == "all done"  # type: ignore[attr-defined]
    assert events[6].session_id == "sdk-123"  # type: ignore[attr-defined]

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
    assert kinds == ["AllyMetricsEvent", "TextEvent", "ErrorEvent"]
    assert "kaboom" in events[2].message  # type: ignore[attr-defined]

    rows = await db.list_messages(db_path, session.id)
    assert [r.kind for r in rows] == ["text", "error"]
    assert json.loads(rows[1].content_json) == {"message": "kaboom"}


async def test_done_event_carries_usage_and_session_id(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")

    _patch_query(
        monkeypatch,
        [_result(session_id="sdk-Z", usage={"input_tokens": 99, "output_tokens": 11})],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    kinds = [type(e).__name__ for e in events]
    assert kinds == ["AllyMetricsEvent", "AllySummaryEvent", "DoneEvent"]
    done = events[-1]
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


# ----- ally events -----


async def _persist_user_text(db_path: Path, session_id: str, text: str) -> None:
    await db.append_message(
        db_path,
        session_id=session_id,
        role="user",
        kind="text",
        content_json=json.dumps({"text": text}),
    )


async def test_first_event_is_metrics_reflecting_sent_user_message(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")
    # Caller (main.py) persists the user message before stream_turn runs.
    await _persist_user_text(db_path, session.id, "one two three")

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="ok")), _result(session_id="sdk-1")],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "one two three")]

    first = events[0]
    assert type(first).__name__ == "AllyMetricsEvent"
    assert first.message_count == 1  # type: ignore[attr-defined]
    assert first.user_words == 3  # type: ignore[attr-defined]
    assert first.agent_words == 0  # type: ignore[attr-defined]
    assert isinstance(first.uk_time, str) and first.uk_time  # type: ignore[attr-defined]


async def test_summary_event_precedes_done_and_carries_analysis_and_refreshed_metrics(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")
    await _persist_user_text(db_path, session.id, "hello there")

    async def fake_analyze(settings: Any, messages: Any) -> AllyAnalysis:
        return AllyAnalysis(topic="ethical hdmi cables", classification="Philosophical")

    monkeypatch.setattr("app.ally.analyze", fake_analyze)

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="four word reply here")), _result(session_id="sdk-2")],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hello there")]

    kinds = [type(e).__name__ for e in events]
    assert kinds[-2:] == ["AllySummaryEvent", "DoneEvent"]
    summary = events[-2]
    assert summary.topic == "ethical hdmi cables"  # type: ignore[attr-defined]
    assert summary.classification == "Philosophical"  # type: ignore[attr-defined]
    # Refreshed metrics include the assistant reply persisted during the turn.
    assert summary.message_count == 2  # type: ignore[attr-defined]
    assert summary.user_words == 2  # type: ignore[attr-defined]
    assert summary.agent_words == 4  # type: ignore[attr-defined]


async def test_text_events_precede_summary_event_non_blocking(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="streamed text")), _result(session_id="sdk-3")],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    kinds = [type(e).__name__ for e in events]
    text_idx = kinds.index("TextEvent")
    summary_idx = kinds.index("AllySummaryEvent")
    assert text_idx < summary_idx


async def test_summary_warning_true_when_in_window_and_scientific(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")

    async def fake_analyze(settings: Any, messages: Any) -> AllyAnalysis:
        return AllyAnalysis(topic="protein folding", classification="Scientific")

    monkeypatch.setattr("app.ally.analyze", fake_analyze)
    monkeypatch.setattr("app.ally.is_in_window", lambda window, now=None: True)

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="ok")), _result(session_id="sdk-4")],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    summary = events[-2]
    assert type(summary).__name__ == "AllySummaryEvent"
    assert summary.warning is True  # type: ignore[attr-defined]


async def test_summary_warning_false_when_outside_window(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")

    async def fake_analyze(settings: Any, messages: Any) -> AllyAnalysis:
        return AllyAnalysis(topic="protein folding", classification="Scientific")

    monkeypatch.setattr("app.ally.analyze", fake_analyze)
    monkeypatch.setattr("app.ally.is_in_window", lambda window, now=None: False)

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="ok")), _result(session_id="sdk-5")],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    summary = events[-2]
    assert type(summary).__name__ == "AllySummaryEvent"
    assert summary.warning is False  # type: ignore[attr-defined]


async def test_analysis_timeout_falls_back_to_placeholder(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await db.create_session(db_path, title="t")

    async def slow_analyze(settings: Any, messages: Any) -> AllyAnalysis:
        await asyncio.sleep(10)
        return AllyAnalysis(topic="never", classification="Scientific")

    monkeypatch.setattr("app.ally.analyze", slow_analyze)
    monkeypatch.setattr("app.agent.ANALYSIS_TIMEOUT_SECONDS", 0.01)

    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text="ok")), _result(session_id="sdk-6")],
    )
    events = [ev async for ev in stream_turn(_settings(), db_path, session, "hi")]

    kinds = [type(e).__name__ for e in events]
    assert kinds[-2:] == ["AllySummaryEvent", "DoneEvent"]
    summary = events[-2]
    from app.ally import TOPIC_PLACEHOLDER

    assert summary.topic == TOPIC_PLACEHOLDER  # type: ignore[attr-defined]
    assert summary.classification == "Other"  # type: ignore[attr-defined]
    # No orphaned side-model task should survive the turn.
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    assert all(t is asyncio.current_task() for t in pending)
