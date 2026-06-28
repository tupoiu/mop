"""Ally domain module: deterministic computations for the Ally summary panel.

This module owns conversation metrics, UK-time computation, and late-window
parsing/evaluation. The async side-model ``analyze()`` and the SSE event
builders live in a later task; the shared dataclass types they consume are
defined here so they can be imported without a circular dependency.

Dependency direction (strict): ``events`` / ``config`` / ``db`` -> ``ally``.
This module must not import ``app.agent`` or ``app.main``.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from app.config import Settings
from app.db import MessageRow
from app.events import AllyMetricsEvent, AllySummaryEvent

logger = logging.getLogger(__name__)

ALLOWED_CLASSES = ("Programming-adjacent", "Philosophical", "Scientific", "Other")
WARNING_CLASSES = frozenset({"Programming-adjacent", "Scientific"})
DEFAULT_LATE_WINDOW = "21:30-05:00"
TOPIC_PLACEHOLDER = "—"

# Bound side-model cost: only the most recent text messages are analysed.
_TRANSCRIPT_MESSAGE_CAP = 20
# Defensive clamp so a runaway topic string never reaches the UI.
_TOPIC_MAX_LEN = 80

_UK_ZONE = ZoneInfo("Europe/London")

_ANALYZE_INSTRUCTION = (
    "Analyse the conversation transcript above. Respond with STRICT JSON only "
    '(no prose, no markdown fences) of the form '
    '{"topic": "<short phrase>", "classification": "<one of: '
    'Programming-adjacent, Philosophical, Scientific, Other>"}. '
    "The topic is a short phrase naming what the conversation is about."
)


@dataclass(frozen=True)
class AllyMetrics:
    agent_words: int
    user_words: int
    message_count: int


@dataclass(frozen=True)
class AllyAnalysis:
    topic: str  # short phrase; TOPIC_PLACEHOLDER on failure
    classification: str  # one of ALLOWED_CLASSES; "Other" on failure/unknown


@dataclass(frozen=True)
class LateWindow:
    start: time  # UK local
    end: time  # UK local


def _message_text(message: MessageRow) -> str:
    """Extract the ``text`` field from a row's ``content_json`` payload.

    Returns an empty string when the payload is malformed or lacks a text field.
    """
    try:
        payload = json.loads(message.content_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text", "")
    return text if isinstance(text, str) else ""


def compute_metrics(messages: list[MessageRow]) -> AllyMetrics:
    """Compute word/message metrics over ``kind == "text"`` user/assistant rows.

    Word counts use ``str.split()``. ``tool_call``/``tool_result``/``error`` rows
    are excluded from both word counts and ``message_count`` (research decision).
    """
    agent_words = 0
    user_words = 0
    message_count = 0
    for message in messages:
        if message.kind != "text":
            continue
        message_count += 1
        word_count = len(_message_text(message).split())
        if message.role == "assistant":
            agent_words += word_count
        elif message.role == "user":
            user_words += word_count
    return AllyMetrics(
        agent_words=agent_words,
        user_words=user_words,
        message_count=message_count,
    )


def current_uk_time(now: datetime | None = None) -> str:
    """Return the current UK (Europe/London) wall-clock time as ``"HH:MM"``.

    When ``now`` is ``None``, current UTC time is used. A supplied ``now`` is
    treated as an aware datetime and converted to Europe/London, reflecting
    BST/GMT correctly.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return now.astimezone(_UK_ZONE).strftime("%H:%M")


def parse_late_window(raw: str) -> LateWindow:
    """Parse ``"HH:MM-HH:MM"`` into a :class:`LateWindow`.

    On missing/empty/malformed input, fall back to parsing
    :data:`DEFAULT_LATE_WINDOW`.
    """
    window = _try_parse_window(raw)
    if window is not None:
        return window
    fallback = _try_parse_window(DEFAULT_LATE_WINDOW)
    assert fallback is not None  # DEFAULT_LATE_WINDOW is always valid
    return fallback


def _try_parse_window(raw: str) -> LateWindow | None:
    if not raw or not raw.strip():
        return None
    parts = raw.strip().split("-")
    if len(parts) != 2:
        return None
    start = _try_parse_time(parts[0])
    end = _try_parse_time(parts[1])
    if start is None or end is None:
        return None
    return LateWindow(start=start, end=end)


def _try_parse_time(raw: str) -> time | None:
    pieces = raw.strip().split(":")
    if len(pieces) != 2:
        return None
    try:
        hour = int(pieces[0])
        minute = int(pieces[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def is_in_window(window: LateWindow, now: datetime | None = None) -> bool:
    """Whether the current UK local time-of-day falls within ``[start, end)``.

    Midnight-crossing aware: when ``start > end`` the window spans midnight, so
    membership is ``t >= start or t < end``; otherwise ``start <= t < end``.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    current = now.astimezone(_UK_ZONE).time()
    if window.start > window.end:
        return current >= window.start or current < window.end
    return window.start <= current < window.end


def evaluate_warning(
    classification: str,
    window: LateWindow,
    now: datetime | None = None,
) -> bool:
    """Return ``True`` iff the class warrants a warning and it's inside the window."""
    return classification in WARNING_CLASSES and is_in_window(window, now)


def _build_transcript(messages: list[MessageRow]) -> str:
    """Build a role-tagged transcript from the last N ``text`` messages."""
    text_rows = [m for m in messages if m.kind == "text"]
    recent = text_rows[-_TRANSCRIPT_MESSAGE_CAP:]
    lines: list[str] = []
    for message in recent:
        text = _message_text(message).strip()
        if not text:
            continue
        lines.append(f"{message.role}: {text}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict[str, object] | None:
    """Extract and parse the first ``{...}`` JSON object embedded in ``text``."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_analysis(text: str) -> AllyAnalysis:
    """Parse a side-model response into a validated :class:`AllyAnalysis`."""
    obj = _extract_json_object(text)
    if obj is None:
        return AllyAnalysis(topic=TOPIC_PLACEHOLDER, classification="Other")

    raw_class = obj.get("classification")
    if isinstance(raw_class, str) and raw_class in ALLOWED_CLASSES:
        classification = raw_class
    else:
        classification = "Other"

    raw_topic = obj.get("topic")
    if isinstance(raw_topic, str) and raw_topic.strip():
        topic = raw_topic.strip()[:_TOPIC_MAX_LEN]
    else:
        topic = TOPIC_PLACEHOLDER

    return AllyAnalysis(topic=topic, classification=classification)


async def analyze(settings: Settings, messages: list[MessageRow]) -> AllyAnalysis:
    """Classify the conversation topic via a single side-model call.

    Never raises to the caller: any failure (network, SDK, malformed/empty
    output, unknown class) collapses to ``AllyAnalysis(TOPIC_PLACEHOLDER,
    "Other")`` (Reqs 2.3, 3.3). The timeout is the caller's responsibility.
    """
    try:
        transcript = _build_transcript(messages)
        prompt = f"{transcript}\n\n{_ANALYZE_INSTRUCTION}"
        options = ClaudeAgentOptions(model=settings.ally_summary_model)
        chunks: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return _coerce_analysis("".join(chunks))
    except Exception:
        logger.warning("ally side-model analysis failed; using placeholder", exc_info=True)
        return AllyAnalysis(topic=TOPIC_PLACEHOLDER, classification="Other")


def metrics_event(messages: list[MessageRow], now: datetime | None = None) -> AllyMetricsEvent:
    """Build the ``ally_metrics`` event from conversation metrics + UK time."""
    metrics = compute_metrics(messages)
    return AllyMetricsEvent(
        agent_words=metrics.agent_words,
        user_words=metrics.user_words,
        message_count=metrics.message_count,
        uk_time=current_uk_time(now),
    )


def summary_event(
    messages: list[MessageRow],
    analysis: AllyAnalysis,
    window: LateWindow,
    now: datetime | None = None,
) -> AllySummaryEvent:
    """Build the ``ally_summary`` event from metrics, analysis, and warning state."""
    metrics = compute_metrics(messages)
    return AllySummaryEvent(
        topic=analysis.topic,
        classification=analysis.classification,
        agent_words=metrics.agent_words,
        user_words=metrics.user_words,
        message_count=metrics.message_count,
        uk_time=current_uk_time(now),
        warning=evaluate_warning(analysis.classification, window, now),
    )
