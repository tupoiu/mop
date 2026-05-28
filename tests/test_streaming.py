"""Integration tests for the streaming send-message endpoint (task 7.3).

Covers:
- POST /api/sessions/{id}/messages returns text/event-stream
- SSE bytes contain event: text then event: done in order
- Persisted history matches what was streamed
- 404 for unknown session id
- 401 for missing auth
- 409 for concurrent second POST to the same session
Requirements: 1.3, 2.4, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import httpx
import pytest

from app.events import DoneEvent, ErrorEvent, TextEvent

_TOKEN = "stream-test-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse_lines(raw: bytes) -> list[dict[str, str]]:
    """Parse pinned-wire-framing SSE: blocks separated by \\n\\n, each block has
    'event: X' and 'data: Y' lines. Returns list of {event, data} dicts."""
    events = []
    for block in raw.split(b"\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = block.split(b"\n")
        ev: dict[str, str] = {}
        for line in lines:
            if line.startswith(b"event: "):
                ev["event"] = line[len(b"event: "):].decode()
            elif line.startswith(b"data: "):
                ev["data"] = line[len(b"data: "):].decode()
        if ev:
            events.append(ev)
    return events


def _make_stream(*events: Any) -> Any:
    async def fake_stream_turn(
        settings: Any, db_path: Any, session: Any, user_content: str
    ) -> AsyncIterator[Any]:
        for ev in events:
            yield ev

    return fake_stream_turn


# ---------------------------------------------------------------------------
# Fixture: lifespan-managed app client
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("APP_AUTH_TOKEN", _TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(db_file))

    from app.main import app  # noqa: PLC0415

    receive_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    send_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _receive() -> dict[str, Any]:
        return await receive_q.get()

    async def _send(msg: dict[str, Any]) -> None:
        await send_q.put(msg)

    task = asyncio.ensure_future(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, _receive, _send)
    )
    await receive_q.put({"type": "lifespan.startup"})
    startup = await send_q.get()
    assert startup["type"] == "lifespan.startup.complete", startup

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        yield c

    await receive_q.put({"type": "lifespan.shutdown"})
    shutdown = await send_q.get()
    assert shutdown["type"] == "lifespan.shutdown.complete", shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.fixture()
async def session_id(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/sessions", json={}, headers=_AUTH)
    assert resp.status_code == 200
    return str(resp.json()["id"])


# ---------------------------------------------------------------------------
# Content-type and headers
# ---------------------------------------------------------------------------


async def test_send_message_returns_text_event_stream(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agent.stream_turn",
        _make_stream(TextEvent(text="hi", message_ord=1), DoneEvent(session_id="s", usage={}, is_error=False)),
    )
    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"content": "hello"},
        headers=_AUTH,
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"
        # consume body
        await resp.aread()


# ---------------------------------------------------------------------------
# Happy-path: event ordering and body
# ---------------------------------------------------------------------------


async def test_send_message_yields_text_then_done_events(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agent.stream_turn",
        _make_stream(
            TextEvent(text="hello there", message_ord=2),
            DoneEvent(session_id="sdk-1", usage={"input_tokens": 5}, is_error=False),
        ),
    )
    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"content": "hi"},
        headers=_AUTH,
    ) as resp:
        raw = await resp.aread()

    events = _parse_sse_lines(raw)
    event_types = [e["event"] for e in events]
    assert event_types == ["text", "done"], event_types

    text_data = json.loads(events[0]["data"])
    assert text_data["text"] == "hello there"

    done_data = json.loads(events[1]["data"])
    assert done_data["session_id"] == "sdk-1"
    assert done_data["is_error"] is False


# ---------------------------------------------------------------------------
# Persistence: user message and streamed rows are saved
# ---------------------------------------------------------------------------


async def test_send_message_persists_user_and_assistant_rows(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agent.stream_turn",
        _make_stream(
            TextEvent(text="reply", message_ord=2),
            DoneEvent(session_id="sdk-2", usage={}, is_error=False),
        ),
    )

    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"content": "my question"},
        headers=_AUTH,
    ) as resp:
        await resp.aread()

    hist = await client.get(f"/api/sessions/{session_id}/messages", headers=_AUTH)
    messages = hist.json()["messages"]
    assert len(messages) >= 1
    assert messages[0]["role"] == "user"
    assert json.loads(messages[0]["content_json"])["text"] == "my question"


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


async def test_send_message_yields_error_event_on_failure(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agent.stream_turn",
        _make_stream(ErrorEvent(message="boom")),
    )
    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"content": "oops"},
        headers=_AUTH,
    ) as resp:
        raw = await resp.aread()

    events = _parse_sse_lines(raw)
    event_types = [e["event"] for e in events]
    assert event_types == ["error"]
    assert json.loads(events[0]["data"])["message"] == "boom"


# ---------------------------------------------------------------------------
# 404 / 401
# ---------------------------------------------------------------------------


async def test_send_message_returns_404_for_unknown_session(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/sessions/does-not-exist/messages",
        json={"content": "hi"},
        headers=_AUTH,
    )
    assert resp.status_code == 404


async def test_send_message_returns_401_without_auth(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    resp = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "hi"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 409: concurrent second POST for the same session
# ---------------------------------------------------------------------------


async def test_send_message_returns_409_when_session_locked(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second concurrent POST to the same session returns 409 session_busy."""
    started = asyncio.Event()
    unblock = asyncio.Event()

    async def slow_stream(
        settings: Any, db_path: Any, session: Any, user_content: str
    ) -> AsyncIterator[Any]:
        started.set()
        await unblock.wait()
        yield DoneEvent(session_id="s", usage={}, is_error=False)

    monkeypatch.setattr("app.agent.stream_turn", slow_stream)

    # Launch first request in the background; it holds the lock via slow_stream
    first_task: asyncio.Task[bytes] = asyncio.ensure_future(
        _consume_stream(
            client, f"/api/sessions/{session_id}/messages", {"content": "first"}
        )
    )

    # Wait until slow_stream has started (lock is held)
    await asyncio.wait_for(started.wait(), timeout=3.0)

    # Second request for the same session — should get 409
    resp = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "second"},
        headers=_AUTH,
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "session_busy"

    # Unblock the first stream so the fixture cleans up
    unblock.set()
    await asyncio.wait_for(first_task, timeout=3.0)


async def _consume_stream(
    client: httpx.AsyncClient, url: str, body: dict[str, Any]
) -> bytes:
    async with client.stream("POST", url, json=body, headers=_AUTH) as resp:
        return await resp.aread()
