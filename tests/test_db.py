import asyncio
import json
from pathlib import Path

import aiosqlite
import pytest

from app.db import (
    MessageRow,
    SessionRow,
    append_message,
    create_session,
    get_session,
    init_db,
    list_messages,
    list_sessions,
    touch_session,
    update_session_sdk_id,
)


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    await init_db(path)
    return path


async def test_init_db_is_idempotent_and_creates_schema(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    await init_db(path)
    await init_db(path)  # must not raise on second call

    async with aiosqlite.connect(path) as conn:
        cursor = await conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "wal"

        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in await cursor.fetchall()]
        assert "sessions" in tables
        assert "messages" in tables

        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
        indexes = {r[0] for r in await cursor.fetchall()}
        assert "idx_messages_session" in indexes
        assert "idx_sessions_updated" in indexes


async def test_create_and_get_session(db_path: Path) -> None:
    session = await create_session(db_path, title="hello")
    assert isinstance(session, SessionRow)
    assert session.title == "hello"
    assert session.sdk_session_id is None
    assert session.created_at == session.updated_at

    fetched = await get_session(db_path, session.id)
    assert fetched == session


async def test_get_unknown_session_returns_none(db_path: Path) -> None:
    assert await get_session(db_path, "no-such-id") is None


async def test_list_sessions_orders_by_updated_at_desc(db_path: Path) -> None:
    first = await create_session(db_path, title="a")
    await asyncio.sleep(0.005)
    second = await create_session(db_path, title="b")

    listed = await list_sessions(db_path)
    assert [s.id for s in listed] == [second.id, first.id]

    await asyncio.sleep(0.005)
    await touch_session(db_path, first.id)
    listed = await list_sessions(db_path)
    assert listed[0].id == first.id


async def test_update_session_sdk_id_persists(db_path: Path) -> None:
    session = await create_session(db_path, title=None)
    await update_session_sdk_id(db_path, session.id, "sdk-abc")

    fetched = await get_session(db_path, session.id)
    assert fetched is not None
    assert fetched.sdk_session_id == "sdk-abc"


async def test_append_and_list_messages_in_order(db_path: Path) -> None:
    session = await create_session(db_path, title="t")

    m1 = await append_message(
        db_path,
        session_id=session.id,
        role="user",
        kind="text",
        content_json=json.dumps({"text": "hi"}),
    )
    m2 = await append_message(
        db_path,
        session_id=session.id,
        role="assistant",
        kind="text",
        content_json=json.dumps({"text": "hello"}),
    )
    m3 = await append_message(
        db_path,
        session_id=session.id,
        role="assistant",
        kind="tool_call",
        content_json=json.dumps({"id": "t1", "name": "echo", "input": {}}),
    )
    m4 = await append_message(
        db_path,
        session_id=session.id,
        role="assistant",
        kind="tool_result",
        content_json=json.dumps({"tool_use_id": "t1", "output": "x", "is_error": False}),
    )
    m5 = await append_message(
        db_path,
        session_id=session.id,
        role="assistant",
        kind="error",
        content_json=json.dumps({"message": "boom"}),
    )

    assert m1.ord < m2.ord < m3.ord < m4.ord < m5.ord
    msgs = await list_messages(db_path, session.id)
    assert [m.ord for m in msgs] == [m1.ord, m2.ord, m3.ord, m4.ord, m5.ord]
    assert all(isinstance(m, MessageRow) for m in msgs)
    assert msgs[2].kind == "tool_call"
    assert msgs[4].kind == "error"


async def test_messages_for_unknown_session_is_empty(db_path: Path) -> None:
    assert await list_messages(db_path, "no-such-id") == []


async def test_append_message_rejects_invalid_kind(db_path: Path) -> None:
    session = await create_session(db_path, title=None)
    with pytest.raises(aiosqlite.IntegrityError):
        await append_message(
            db_path,
            session_id=session.id,
            role="user",
            kind="garbage",  # type: ignore[arg-type]
            content_json="{}",
        )
