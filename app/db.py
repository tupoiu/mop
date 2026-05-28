import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Literal

import aiosqlite

Role = Literal["user", "assistant"]
Kind = Literal["text", "tool_call", "tool_result", "error"]


@dataclass(frozen=True)
class SessionRow:
    id: str
    title: str | None
    sdk_session_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MessageRow:
    ord: int
    session_id: str
    role: str
    kind: str
    content_json: str
    created_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    sdk_session_id  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    ord             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
    kind            TEXT NOT NULL CHECK (kind IN ('text','tool_call','tool_result','error')),
    content_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ord);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def _connect(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn


async def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(_SCHEMA)
        await conn.commit()


async def create_session(path: Path, *, title: str | None = None) -> SessionRow:
    session_id = uuid.uuid4().hex
    now = _now()
    async with _connect(path) as conn:
        await conn.execute(
            "INSERT INTO sessions (id, title, sdk_session_id, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?)",
            (session_id, title, now, now),
        )
        await conn.commit()
    return SessionRow(
        id=session_id,
        title=title,
        sdk_session_id=None,
        created_at=now,
        updated_at=now,
    )


async def get_session(path: Path, session_id: str) -> SessionRow | None:
    async with _connect(path) as conn:
        cursor = await conn.execute(
            "SELECT id, title, sdk_session_id, created_at, updated_at "
            "FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return SessionRow(
        id=row[0],
        title=row[1],
        sdk_session_id=row[2],
        created_at=row[3],
        updated_at=row[4],
    )


async def list_sessions(path: Path) -> list[SessionRow]:
    async with _connect(path) as conn:
        cursor = await conn.execute(
            "SELECT id, title, sdk_session_id, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
    return [
        SessionRow(
            id=r[0],
            title=r[1],
            sdk_session_id=r[2],
            created_at=r[3],
            updated_at=r[4],
        )
        for r in rows
    ]


async def update_session_sdk_id(path: Path, session_id: str, sdk_session_id: str) -> None:
    async with _connect(path) as conn:
        await conn.execute(
            "UPDATE sessions SET sdk_session_id = ?, updated_at = ? WHERE id = ?",
            (sdk_session_id, _now(), session_id),
        )
        await conn.commit()


async def touch_session(path: Path, session_id: str) -> None:
    async with _connect(path) as conn:
        await conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        await conn.commit()


async def append_message(
    path: Path,
    *,
    session_id: str,
    role: Role,
    kind: Kind,
    content_json: str,
) -> MessageRow:
    now = _now()
    async with _connect(path) as conn:
        cursor = await conn.execute(
            "INSERT INTO messages (session_id, role, kind, content_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, kind, content_json, now),
        )
        rowid = cursor.lastrowid
        await conn.commit()
    if rowid is None:
        raise RuntimeError("INSERT did not return a rowid")
    return MessageRow(
        ord=rowid,
        session_id=session_id,
        role=role,
        kind=kind,
        content_json=content_json,
        created_at=now,
    )


async def list_messages(path: Path, session_id: str) -> list[MessageRow]:
    async with _connect(path) as conn:
        cursor = await conn.execute(
            "SELECT ord, session_id, role, kind, content_json, created_at "
            "FROM messages WHERE session_id = ? ORDER BY ord ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
    return [
        MessageRow(
            ord=r[0],
            session_id=r[1],
            role=r[2],
            kind=r[3],
            content_json=r[4],
            created_at=r[5],
        )
        for r in rows
    ]
