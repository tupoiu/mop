import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock

from app.ally import (
    ALLOWED_CLASSES,
    DEFAULT_LATE_WINDOW,
    TOPIC_PLACEHOLDER,
    WARNING_CLASSES,
    AllyAnalysis,
    AllyMetrics,
    LateWindow,
    analyze,
    compute_metrics,
    current_uk_time,
    evaluate_warning,
    is_in_window,
    metrics_event,
    parse_late_window,
    summary_event,
)
from app.config import Settings
from app.db import MessageRow
from app.events import AllyMetricsEvent, AllySummaryEvent


def _msg(role: str, kind: str, text: str | None = None, ord_: int = 0) -> MessageRow:
    if text is None:
        content = json.dumps({"name": "tool", "input": {}})
    else:
        content = json.dumps({"text": text})
    return MessageRow(
        ord=ord_,
        session_id="s1",
        role=role,
        kind=kind,
        content_json=content,
        created_at="2026-06-27T00:00:00+00:00",
    )


# --- Module-level constants -------------------------------------------------


def test_constants():
    assert ALLOWED_CLASSES == (
        "Programming-adjacent",
        "Philosophical",
        "Scientific",
        "Other",
    )
    assert WARNING_CLASSES == frozenset({"Programming-adjacent", "Scientific"})
    assert DEFAULT_LATE_WINDOW == "21:30-05:00"
    assert TOPIC_PLACEHOLDER == "—"


def test_dataclasses_are_frozen():
    metrics = AllyMetrics(agent_words=1, user_words=2, message_count=3)
    analysis = AllyAnalysis(topic="x", classification="Other")
    window = LateWindow(start=time(21, 30), end=time(5, 0))
    assert metrics.agent_words == 1
    assert analysis.topic == "x"
    assert window.start == time(21, 30)


# --- compute_metrics --------------------------------------------------------


def test_compute_metrics_counts_words_and_messages():
    messages = [
        _msg("user", "text", "hello there friend", 1),  # 3 user words
        _msg("assistant", "text", "hi how are you", 2),  # 4 agent words
        _msg("user", "text", "two words", 3),  # 2 user words
    ]
    metrics = compute_metrics(messages)
    assert metrics.user_words == 5
    assert metrics.agent_words == 4
    assert metrics.message_count == 3


def test_compute_metrics_excludes_tool_and_error_rows():
    messages = [
        _msg("user", "text", "one two", 1),  # counted: 2 user words
        _msg("assistant", "tool_call", None, 2),  # excluded
        _msg("user", "tool_result", None, 3),  # excluded
        _msg("assistant", "error", None, 4),  # excluded
        _msg("assistant", "text", "alpha beta gamma", 5),  # counted: 3 agent words
    ]
    metrics = compute_metrics(messages)
    assert metrics.user_words == 2
    assert metrics.agent_words == 3
    assert metrics.message_count == 2


def test_compute_metrics_empty_conversation():
    metrics = compute_metrics([])
    assert metrics == AllyMetrics(agent_words=0, user_words=0, message_count=0)


# --- current_uk_time --------------------------------------------------------


def test_current_uk_time_bst():
    # July: BST (UTC+1). 12:00 UTC -> 13:00 London.
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert current_uk_time(now) == "13:00"


def test_current_uk_time_gmt():
    # January: GMT (UTC+0). 12:00 UTC -> 12:00 London.
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert current_uk_time(now) == "12:00"


def test_current_uk_time_none_returns_hhmm():
    value = current_uk_time(None)
    assert len(value) == 5
    assert value[2] == ":"


# --- parse_late_window ------------------------------------------------------


def test_parse_late_window_valid():
    window = parse_late_window("22:00-04:30")
    assert window == LateWindow(start=time(22, 0), end=time(4, 30))


def test_parse_late_window_invalid_falls_back_to_default():
    window = parse_late_window("not-a-window")
    assert window == LateWindow(start=time(21, 30), end=time(5, 0))


def test_parse_late_window_empty_falls_back_to_default():
    window = parse_late_window("")
    assert window == LateWindow(start=time(21, 30), end=time(5, 0))


def test_parse_late_window_out_of_range_falls_back():
    window = parse_late_window("25:00-30:00")
    assert window == LateWindow(start=time(21, 30), end=time(5, 0))


# --- is_in_window -----------------------------------------------------------


def _at(hhmm: tuple[int, int]) -> datetime:
    # An aware UTC datetime in January (GMT) so London local == UTC time.
    return datetime(2026, 1, 15, hhmm[0], hhmm[1], tzinfo=timezone.utc)


def test_is_in_window_midnight_crossing_inside():
    window = parse_late_window(DEFAULT_LATE_WINDOW)  # 21:30-05:00
    assert is_in_window(window, _at((22, 0))) is True
    assert is_in_window(window, _at((3, 0))) is True


def test_is_in_window_midnight_crossing_outside():
    window = parse_late_window(DEFAULT_LATE_WINDOW)
    assert is_in_window(window, _at((12, 0))) is False
    assert is_in_window(window, _at((6, 0))) is False


def test_is_in_window_boundaries():
    window = parse_late_window(DEFAULT_LATE_WINDOW)
    assert is_in_window(window, _at((21, 30))) is True  # start inclusive
    assert is_in_window(window, _at((5, 0))) is False  # end exclusive


def test_is_in_window_non_crossing():
    window = LateWindow(start=time(9, 0), end=time(17, 0))
    assert is_in_window(window, _at((12, 0))) is True
    assert is_in_window(window, _at((8, 0))) is False
    assert is_in_window(window, _at((17, 0))) is False  # end exclusive


# --- evaluate_warning -------------------------------------------------------


def test_evaluate_warning_matrix_inside_window():
    window = parse_late_window(DEFAULT_LATE_WINDOW)
    inside = _at((23, 0))
    assert evaluate_warning("Programming-adjacent", window, inside) is True
    assert evaluate_warning("Scientific", window, inside) is True
    assert evaluate_warning("Philosophical", window, inside) is False
    assert evaluate_warning("Other", window, inside) is False


def test_evaluate_warning_matrix_outside_window():
    window = parse_late_window(DEFAULT_LATE_WINDOW)
    outside = _at((12, 0))
    for cls in ALLOWED_CLASSES:
        assert evaluate_warning(cls, window, outside) is False


# --- analyze ----------------------------------------------------------------


def _settings() -> Settings:
    return Settings(
        app_auth_token="t",
        anthropic_api_key="k",
        conversations_db_path=Path("/dev/null"),
        anthropic_model=None,
    )


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


def _patch_query(
    monkeypatch: pytest.MonkeyPatch,
    messages: list[Any],
    capture: list[Any] | None = None,
) -> None:
    async def fake_query(*, prompt: str, options: Any = None) -> AsyncIterator[Any]:
        if capture is not None:
            capture.append((prompt, options))
        for m in messages:
            yield m

    monkeypatch.setattr("app.ally.query", fake_query)


async def test_analyze_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text='{"topic": "Async IO", "classification": "Scientific"}'))],
    )
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result == AllyAnalysis(topic="Async IO", classification="Scientific")


async def test_analyze_json_wrapped_in_prose(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_query(
        monkeypatch,
        [
            _assistant(
                TextBlock(
                    text='Sure! Here is the result:\n```json\n'
                    '{"topic": "Recursion", "classification": "Programming-adjacent"}\n```'
                )
            )
        ],
    )
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result == AllyAnalysis(topic="Recursion", classification="Programming-adjacent")


async def test_analyze_uses_summary_model_and_caps_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: list[Any] = []
    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text='{"topic": "x", "classification": "Other"}'))],
        capture=capture,
    )
    settings = Settings(
        app_auth_token="t",
        anthropic_api_key="k",
        conversations_db_path=Path("/dev/null"),
        anthropic_model=None,
        ally_summary_model="my-summary-model",
    )
    # 30 text messages — only the last 20 should appear in the transcript.
    messages = [_msg("user", "text", f"word{i}", i) for i in range(30)]
    await analyze(settings, messages)
    prompt, options = capture[0]
    assert options.model == "my-summary-model"
    assert "word29" in prompt
    assert "word9" not in prompt  # capped to last 20 (word10..word29)


async def test_analyze_malformed_json_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_query(monkeypatch, [_assistant(TextBlock(text="not json at all"))])
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result == AllyAnalysis(topic=TOPIC_PLACEHOLDER, classification="Other")


async def test_analyze_empty_output_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_query(monkeypatch, [_assistant()])
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result == AllyAnalysis(topic=TOPIC_PLACEHOLDER, classification="Other")


async def test_analyze_unknown_classification_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text='{"topic": "Cooking", "classification": "Culinary"}'))],
    )
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result == AllyAnalysis(topic="Cooking", classification="Other")


async def test_analyze_missing_topic_uses_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text='{"classification": "Philosophical"}'))],
    )
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result == AllyAnalysis(topic=TOPIC_PLACEHOLDER, classification="Philosophical")


async def test_analyze_query_raises_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*, prompt: str, options: Any = None) -> AsyncIterator[Any]:
        raise RuntimeError("network down")
        yield  # pragma: no cover

    monkeypatch.setattr("app.ally.query", boom)
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result == AllyAnalysis(topic=TOPIC_PLACEHOLDER, classification="Other")


async def test_analyze_clamps_overly_long_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    long_topic = "z" * 500
    _patch_query(
        monkeypatch,
        [_assistant(TextBlock(text=json.dumps({"topic": long_topic, "classification": "Other"})))],
    )
    result = await analyze(_settings(), [_msg("user", "text", "hi", 1)])
    assert result.classification == "Other"
    assert len(result.topic) < len(long_topic)


# --- metrics_event ----------------------------------------------------------


def test_metrics_event_builds_counts_and_time():
    messages = [
        _msg("user", "text", "hello there friend", 1),
        _msg("assistant", "text", "hi how are you", 2),
    ]
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    event = metrics_event(messages, now)
    assert isinstance(event, AllyMetricsEvent)
    assert event.user_words == 3
    assert event.agent_words == 4
    assert event.message_count == 2
    assert event.uk_time == "12:00"


# --- summary_event ----------------------------------------------------------


def test_summary_event_warning_true_for_late_scientific():
    window = parse_late_window(DEFAULT_LATE_WINDOW)
    late = _at((23, 0))
    messages = [_msg("user", "text", "two words", 1)]
    analysis = AllyAnalysis(topic="Quantum", classification="Scientific")
    event = summary_event(messages, analysis, window, late)
    assert isinstance(event, AllySummaryEvent)
    assert event.topic == "Quantum"
    assert event.classification == "Scientific"
    assert event.user_words == 2
    assert event.message_count == 1
    assert event.uk_time == "23:00"
    assert event.warning is True


def test_summary_event_warning_false_daytime():
    window = parse_late_window(DEFAULT_LATE_WINDOW)
    day = _at((12, 0))
    analysis = AllyAnalysis(topic="Quantum", classification="Scientific")
    event = summary_event([], analysis, window, day)
    assert event.warning is False


def test_summary_event_warning_false_philosophical_late():
    window = parse_late_window(DEFAULT_LATE_WINDOW)
    late = _at((23, 0))
    analysis = AllyAnalysis(topic="Free Will", classification="Philosophical")
    event = summary_event([], analysis, window, late)
    assert event.warning is False
