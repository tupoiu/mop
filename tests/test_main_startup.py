"""Integration tests for app/main.py — startup wiring and static mount.

RED phase written before app/main.py existed; GREEN phase validates the implementation.
Uses httpx.AsyncClient with a manual ASGI lifespan call to properly exercise startup.
"""

import asyncio
from typing import AsyncIterator

import httpx
import pytest


@pytest.fixture(autouse=True)
def _required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide minimal env vars so load_settings() succeeds during import."""
    monkeypatch.setenv("APP_AUTH_TOKEN", "test-token-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", ":memory:")


@pytest.fixture()
async def lifespan_app(_required_env: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: RUF029
    """Start the FastAPI app via its ASGI lifespan, then yield an httpx client."""
    # Import after env vars are patched
    from app.main import app  # noqa: PLC0415

    # Manually drive the ASGI lifespan protocol so app.state is populated
    startup_complete: asyncio.Event = asyncio.Event()
    shutdown_complete: asyncio.Event = asyncio.Event()

    receive_queue: asyncio.Queue[dict] = asyncio.Queue()  # type: ignore[type-arg]
    send_queue: asyncio.Queue[dict] = asyncio.Queue()  # type: ignore[type-arg]

    async def receive() -> dict:  # type: ignore[return]
        return await receive_queue.get()

    async def send(message: dict) -> None:  # type: ignore[type-arg]
        await send_queue.put(message)

    # Start lifespan in background
    async def run_lifespan() -> None:
        await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)

    task = asyncio.ensure_future(run_lifespan())

    # Send startup event
    await receive_queue.put({"type": "lifespan.startup"})
    msg = await send_queue.get()
    assert msg["type"] == "lifespan.startup.complete", f"Unexpected: {msg}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        yield client

    # Send shutdown event
    await receive_queue.put({"type": "lifespan.shutdown"})
    msg = await send_queue.get()
    assert msg["type"] == "lifespan.shutdown.complete", f"Unexpected: {msg}"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Test 1: GET / returns 200 with text/html content
# ---------------------------------------------------------------------------
async def test_root_returns_200_html(lifespan_app: httpx.AsyncClient) -> None:
    response = await lifespan_app.get("/")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/html" in content_type, f"Expected text/html, got: {content_type!r}"


# ---------------------------------------------------------------------------
# Test 2: GET / response body contains index.html content
# ---------------------------------------------------------------------------
async def test_root_serves_index_html(lifespan_app: httpx.AsyncClient) -> None:
    response = await lifespan_app.get("/")
    assert response.status_code == 200
    body = response.text
    # index.html contains these identifiable strings
    assert "<title>Claude Agent</title>" in body
    assert 'id="app"' in body


# ---------------------------------------------------------------------------
# Test 3: app.state.settings is a Settings instance after startup
# ---------------------------------------------------------------------------
async def test_app_state_settings(lifespan_app: httpx.AsyncClient) -> None:
    from app.config import Settings  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    assert isinstance(app.state.settings, Settings)
    assert app.state.settings.app_auth_token == "test-token-secret"


# ---------------------------------------------------------------------------
# Test 4: app.state.session_locks is an empty dict after startup
# ---------------------------------------------------------------------------
async def test_app_state_session_locks(lifespan_app: httpx.AsyncClient) -> None:
    from app.main import app  # noqa: PLC0415

    assert isinstance(app.state.session_locks, dict)


# ---------------------------------------------------------------------------
# Test 5: session_locks is empty at startup (lazily populated per session)
# ---------------------------------------------------------------------------
async def test_session_locks_is_empty_at_startup(lifespan_app: httpx.AsyncClient) -> None:
    from app.main import app  # noqa: PLC0415

    assert app.state.session_locks == {}
