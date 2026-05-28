"""FastAPI application entry point.

Responsibilities:
- Lifespan: load settings, init DB, import app.tools (triggers auto-discovery),
  stash settings + session_locks on app.state.
- Static mount: serve frontend/ so GET / delivers index.html.
- Route registration: all /api/* routes are defined here and protected by
  the require_token dependency.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing_extensions import TypedDict

from app import agent
from app.auth import require_token
from app.config import Settings, load_settings
from app.db import append_message, create_session, get_session, list_messages, list_sessions
from app.db import init_db
from app.events import serialize

logger = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------

class _SessionOut(TypedDict):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class _MessageOut(TypedDict):
    ord: int
    session_id: str
    role: str
    kind: str
    content_json: str
    created_at: str


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class _CreateSessionBody(BaseModel):
    title: str | None = None


class _SendMessageBody(BaseModel):
    content: str


# ---------------------------------------------------------------------------
# API router  (registered before the static mount so it takes precedence)
# ---------------------------------------------------------------------------

_api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


@_api.post("/sessions")
async def _create_session(request: Request, body: _CreateSessionBody = _CreateSessionBody()) -> _SessionOut:
    db = request.app.state.settings.conversations_db_path
    row = await create_session(db, title=body.title)
    return _SessionOut(id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at)


@_api.get("/sessions")
async def _list_sessions(request: Request) -> dict[str, list[_SessionOut]]:
    db = request.app.state.settings.conversations_db_path
    rows = await list_sessions(db)
    return {
        "sessions": [
            _SessionOut(id=r.id, title=r.title, created_at=r.created_at, updated_at=r.updated_at)
            for r in rows
        ]
    }


@_api.get("/sessions/{session_id}/messages")
async def _get_messages(request: Request, session_id: str) -> dict[str, list[_MessageOut]]:
    db = request.app.state.settings.conversations_db_path
    if await get_session(db, session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    rows = await list_messages(db, session_id)
    return {
        "messages": [
            _MessageOut(
                ord=m.ord,
                session_id=m.session_id,
                role=m.role,
                kind=m.kind,
                content_json=m.content_json,
                created_at=m.created_at,
            )
            for m in rows
        ]
    }


@_api.post("/sessions/{session_id}/messages", response_model=None)
async def _send_message(
    request: Request,
    session_id: str,
    body: _SendMessageBody,
) -> StreamingResponse | JSONResponse:
    settings: Settings = request.app.state.settings
    db_path = settings.conversations_db_path

    session = await get_session(db_path, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    await append_message(
        db_path,
        session_id=session_id,
        role="user",
        kind="text",
        content_json=json.dumps({"text": body.content}),
    )

    locks: dict[str, asyncio.Lock] = request.app.state.session_locks
    if session_id not in locks:
        locks[session_id] = asyncio.Lock()
    lock = locks[session_id]

    if lock.locked():
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "session_busy"},
        )
    await lock.acquire()

    async def _generate() -> AsyncIterator[bytes]:
        try:
            async for event in agent.stream_turn(settings, db_path, session, body.content):
                if await request.is_disconnected():
                    break
                yield serialize(event)
        finally:
            lock.release()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle handler.

    Startup order:
    1. Load and validate settings from env vars.
    2. Initialise the SQLite database (idempotent DDL).
    3. Import app.tools, which runs auto-discovery at module load time and logs
       the discovered tools (echo, read_url, …).
    4. Stash settings and an empty session_locks dict on app.state so route
       handlers can access them without importing global state.
    """
    settings: Settings = load_settings()
    logger.info("Settings loaded (model=%s, db=%s)", settings.anthropic_model, settings.conversations_db_path)

    await init_db(settings.conversations_db_path)
    logger.info("Database initialised at %s", settings.conversations_db_path)

    import app.tools as _tools  # noqa: PLC0415

    logger.info(
        "Tool discovery complete: %d tool(s) registered — %s",
        len(_tools.ALLOWED_TOOLS),
        _tools.ALLOWED_TOOLS,
    )

    app.state.settings = settings
    session_locks: dict[str, asyncio.Lock] = {}
    app.state.session_locks = session_locks

    yield

    logger.info("Shutting down.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Factory that creates and configures the FastAPI application."""
    application = FastAPI(title="Claude Agent Web App", lifespan=_lifespan)

    @application.middleware("http")
    async def _security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Force revalidation of static assets so edits to frontend files are
        # picked up immediately without a hard reload.
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # API routes must be registered before the static mount — FastAPI resolves
    # routes in registration order and the catch-all static mount would shadow
    # any route added after it.
    application.include_router(_api)

    application.mount(
        "/",
        StaticFiles(directory=str(_FRONTEND_DIR), html=True),
        name="frontend",
    )

    return application


app: FastAPI = create_app()
