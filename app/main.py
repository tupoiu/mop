"""FastAPI application entry point.

Responsibilities:
- Lifespan: load settings, init DB, import app.tools (triggers auto-discovery),
  stash settings + session_locks on app.state.
- Static mount: serve frontend/ so GET / delivers index.html.
- Route registration: all /api/* routes are defined here and protected by
  the require_token dependency.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings, load_settings
from app.db import init_db

logger = logging.getLogger(__name__)

# Path to the frontend directory — resolve relative to this file so it works
# regardless of the working directory uvicorn is launched from.
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


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
    # 1. Settings
    settings: Settings = load_settings()
    logger.info("Settings loaded (model=%s, db=%s)", settings.anthropic_model, settings.conversations_db_path)

    # 2. DB
    await init_db(settings.conversations_db_path)
    logger.info("Database initialised at %s", settings.conversations_db_path)

    # 3. Tool discovery (import triggers walk_package at module level)
    import app.tools as _tools  # noqa: PLC0415

    logger.info(
        "Tool discovery complete: %d tool(s) registered — %s",
        len(_tools.ALLOWED_TOOLS),
        _tools.ALLOWED_TOOLS,
    )

    # 4. Stash on app.state
    app.state.settings = settings
    session_locks: dict[str, asyncio.Lock] = {}
    app.state.session_locks = session_locks

    yield  # application runs here

    # Shutdown — nothing to tear down for now.
    logger.info("Shutting down.")


def create_app() -> FastAPI:
    """Factory that creates and configures the FastAPI application."""
    application = FastAPI(title="Claude Agent Web App", lifespan=_lifespan)

    # Mount the SPA — html=True enables fallback to index.html for unknown paths
    # and automatically serves index.html for GET /.
    application.mount(
        "/",
        StaticFiles(directory=str(_FRONTEND_DIR), html=True),
        name="frontend",
    )

    return application


app: FastAPI = create_app()
