"""Integration tests for the session API routes (task 7.2).

Covers: POST /api/sessions, GET /api/sessions, GET /api/sessions/{id}/messages.
Uses httpx.AsyncClient against the real ASGI app with a per-test SQLite file so
rows created in one call are visible to subsequent calls within the same test.

Requirements: 1.3, 2.1, 2.2, 2.3, 2.4
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest

_TOKEN = "routes-test-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture()
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    """Start the app via its ASGI lifespan, yield an authenticated client."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("APP_AUTH_TOKEN", _TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(db_file))

    from app.main import app  # noqa: PLC0415

    receive_q: asyncio.Queue[dict] = asyncio.Queue()  # type: ignore[type-arg]
    send_q: asyncio.Queue[dict] = asyncio.Queue()  # type: ignore[type-arg]

    async def _receive() -> dict:  # type: ignore[return]
        return await receive_q.get()

    async def _send(msg: dict) -> None:  # type: ignore[type-arg]
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


# ---------------------------------------------------------------------------
# POST /api/sessions
# ---------------------------------------------------------------------------

async def test_create_session_returns_session_fields(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={}, headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "title" in body
    assert "created_at" in body
    assert "updated_at" in body
    assert "sdk_session_id" not in body


async def test_create_session_with_title(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={"title": "My Session"}, headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["title"] == "My Session"


async def test_create_session_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={})
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_create_session_rejects_wrong_token(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={}, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/sessions
# ---------------------------------------------------------------------------

async def test_list_sessions_includes_created_session(client: httpx.AsyncClient) -> None:
    session_id = (await client.post("/api/sessions", json={}, headers=_AUTH)).json()["id"]

    resp = await client.get("/api/sessions", headers=_AUTH)
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["sessions"]]
    assert session_id in ids


async def test_list_sessions_ordered_most_recent_first(client: httpx.AsyncClient) -> None:
    id1 = (await client.post("/api/sessions", json={}, headers=_AUTH)).json()["id"]
    await asyncio.sleep(0.005)  # ensure distinct updated_at timestamps
    id2 = (await client.post("/api/sessions", json={}, headers=_AUTH)).json()["id"]

    ids = [s["id"] for s in (await client.get("/api/sessions", headers=_AUTH)).json()["sessions"]]
    assert ids.index(id2) < ids.index(id1)


async def test_list_sessions_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/sessions")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/sessions/{id}/messages
# ---------------------------------------------------------------------------

async def test_get_messages_empty_for_new_session(client: httpx.AsyncClient) -> None:
    session_id = (await client.post("/api/sessions", json={}, headers=_AUTH)).json()["id"]
    resp = await client.get(f"/api/sessions/{session_id}/messages", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"messages": []}


async def test_get_messages_returns_404_for_unknown_id(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/sessions/does-not-exist/messages", headers=_AUTH)
    assert resp.status_code == 404


async def test_get_messages_requires_auth(client: httpx.AsyncClient) -> None:
    session_id = (await client.post("/api/sessions", json={}, headers=_AUTH)).json()["id"]
    resp = await client.get(f"/api/sessions/{session_id}/messages")
    assert resp.status_code == 401
