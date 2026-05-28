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
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"




def _build_stub_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/sessions")
    async def create_session() -> dict[str, object]:
        return {
            "id": "stub-session",
            "title": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    @app.get("/api/sessions")
    async def list_sessions() -> dict[str, list[object]]:
        return {"sessions": []}

    @app.get("/api/sessions/{session_id}/messages")
    async def get_messages(session_id: str) -> dict[str, list[object]]:
        return {"messages": []}

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
