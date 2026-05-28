"""End-to-end test harness.

Spins up a minimal FastAPI stub backend in a background thread that:
- Serves the real `frontend/` directory at `/` (so the SPA loads exactly as it
  would in production).
- Stubs the `/api/*` surface with minimal responses so the shell can render
  without a real agent or database.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing_extensions import TypedDict

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse


class _SessionRow(TypedDict):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class _MessageRow(TypedDict):
    session_id: str
    role: str
    kind: str
    content_json: str
    created_at: str

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Pinned SSE fixture used by test_sse_consumer.py. Each chunk is one complete
# event block (event + data + blank line). The order mirrors a real turn:
# text preamble → tool call → tool result → text response → done.
_SSE_FIXTURE_CHUNKS: list[bytes] = [
    b'event: text\ndata: {"text": "Hello ", "message_ord": 1}\n\n',
    b'event: tool_call\ndata: {"id": "tc1", "name": "echo", "input": {"text": "hi"}, "message_ord": 2}\n\n',
    b'event: tool_result\ndata: {"tool_use_id": "tc1", "output": "hi", "is_error": false, "message_ord": 3}\n\n',
    b'event: text\ndata: {"text": "world", "message_ord": 4}\n\n',
    b'event: done\ndata: {"session_id": "stub-session", "usage": {}, "is_error": false}\n\n',
]


def _build_stub_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/sessions")
    async def create_session() -> _SessionRow:
        return _SessionRow(
            id="stub-session",
            title=None,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )

    @app.get("/api/sessions")
    async def list_sessions() -> dict[str, list[_SessionRow]]:
        return {
            "sessions": [
                _SessionRow(
                    id="stub-session",
                    title=None,
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                )
            ]
        }

    @app.get("/api/sessions/{session_id}/messages")
    async def get_messages(session_id: str) -> dict[str, list[_MessageRow]]:
        return {"messages": []}

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str) -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            for chunk in _SSE_FIXTURE_CHUNKS:
                yield chunk

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def stub_url() -> Iterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        app=_build_stub_app(),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("stub backend failed to start within 5s")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)
