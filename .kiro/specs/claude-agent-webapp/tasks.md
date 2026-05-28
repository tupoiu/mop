# Implementation Plan

- [ ] 1. Foundation: project scaffolding and dev task runner
- [x] 1.1 Scaffold the Python project layout, dependencies, and packaging
  - Create `pyproject.toml` with the runtime dependencies pinned by the design (`claude-agent-sdk` ≥0.1.80, `fastapi`, `uvicorn`, `sse-starlette`, `aiosqlite`, `httpx`) and dev dependencies (`pytest`, `pytest-asyncio`, `httpx[asyncio]`, `poethepoet`, `python-dotenv`)
  - Create the empty package skeleton (`app/__init__.py`, `app/tools/__init__.py` placeholder, `tests/__init__.py`) and a `.gitignore` that excludes `conversations.db`, `.env`, and the lockfile cache
  - Generate the `uv` lockfile
  - Observable: `uv sync` succeeds against the locked dependency set on a clean checkout, and `python -c "import app, app.tools"` runs without errors
  - _Requirements: 6.3_

- [x] 1.2 Provide `.env.example` enumerating every environment variable
  - List `APP_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `CONVERSATIONS_DB_PATH`, `ANTHROPIC_MODEL` with one-line comments and sensible non-secret defaults where applicable
  - Observable: `.env.example` is present at the repo root and every variable read by the app appears in it
  - _Requirements: 6.3_

- [x] 1.3 Configure `poe` task runner with help-described tasks
  - Define `[tool.poe.tasks]` for `dev`, `demo`, `test`, `lint`, `format`, `db-reset`, each with a `help` field
  - `dev` runs uvicorn with auto-reload bound to a local port; `demo` runs uvicorn with deterministic config (loads `.env`, default DB path); `test` runs pytest; `lint`/`format` invoke the project's chosen tools (e.g. ruff check / ruff format); `db-reset` deletes the SQLite file at `CONVERSATIONS_DB_PATH`
  - Observable: `poe --help` lists all six tasks with their descriptions, and `poe test` runs the pytest collector successfully (even before tests exist)
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [ ] 2. Foundation: configuration and edge primitives
- [ ] 2.1 Implement settings loading and startup validation
  - Read `APP_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `CONVERSATIONS_DB_PATH` (default `./conversations.db`), and optional `ANTHROPIC_MODEL` from the environment
  - Raise a `RuntimeError` naming the missing variable when either required env var is unset or empty
  - Observable: importing and calling the loader with both required vars set returns a frozen settings object; with either var missing it raises with a message that names the offending variable
  - _Requirements: 1.1, 1.2, 6.1, 6.2_

- [ ] 2.2 Implement the bearer-token authentication dependency
  - Compare the incoming `Authorization: Bearer …` header against the configured token using `secrets.compare_digest`
  - Reject mismatches and missing headers with HTTP 401 plus a `WWW-Authenticate: Bearer` response header
  - Observable: a unit test driving the dependency directly returns 401 on missing/wrong tokens and passes through on the configured token
  - _Requirements: 1.3_
  - _Depends: 2.1_

- [ ] 2.3 Implement the SSE event module with pinned wire framing
  - Define event types (`text`, `tool_call`, `tool_result`, `done`, `error`) and a serializer that emits exactly `event: <type>\ndata: <json>\n\n` (UTF-8, single-line JSON, no `id:`/`retry:`/comment lines)
  - Observable: a round-trip unit test serializes one event of each type and parses it back via a minimal `\n\n`-splitter that recovers the original payload verbatim
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 3. Foundation: SQLite schema and persistence helpers
- [ ] 3.1 Initialize the SQLite schema and connection helpers
  - Implement an `init_db` routine that creates the `sessions` and `messages` tables (with the constraints, indexes, and `PRAGMA journal_mode=WAL` / `PRAGMA foreign_keys=ON` from the design) idempotently
  - Resolve the DB file path from `CONVERSATIONS_DB_PATH` so it can point at a mounted volume
  - Observable: running init twice against a fresh path leaves both tables present, `journal_mode` returns `wal`, and the indexes exist
  - _Requirements: 2.5_
  - _Depends: 2.1_

- [ ] 3.2 Implement session and message CRUD helpers
  - Implement create/list/get for sessions, append/list for messages, plus `update_session_sdk_id` and `touch_session`
  - Sessions list returns rows ordered by `updated_at DESC`; messages list returns rows in `ord ASC` for one session id
  - `append_message` accepts the four `kind` values (`text`, `tool_call`, `tool_result`, `error`) and a JSON-encoded content payload
  - Observable: a round-trip test creates a session, appends one message of each kind, lists messages back in insertion order, and verifies sessions reorder after `touch_session`
  - _Requirements: 2.1, 2.2, 2.3, 2.5, 3.6_

- [ ] 4. Core: custom tool auto-discovery and example tools
- [ ] 4.1 (P) Author the `echo` and `read_url` example tools
  - `echo` returns its input string unchanged using the SDK `@tool` decorator and exports `TOOLS = [echo]`
  - `read_url` fetches a URL with `httpx.AsyncClient` (timeout, follow redirects, response body capped at 200 KB) and exports `TOOLS = [read_url]`
  - Observable: each module imports cleanly and exposes a non-empty `TOOLS` list whose entries are SDK-recognized tool objects
  - _Requirements: 4.2, 4.3_
  - _Boundary: app/tools/echo.py, app/tools/read_url.py_

- [ ] 4.2 (P) Implement tool auto-discovery and SDK MCP server assembly
  - Walk `app.tools` with `pkgutil.iter_modules`, import each submodule under try/except, concatenate every `TOOLS` list found, and build one in-process server via `create_sdk_mcp_server(name="local", version="0.1.0", tools=...)`
  - Compute `ALLOWED_TOOLS` as `["mcp__local__<name>", ...]` for every successfully registered tool
  - Log and skip modules that fail to import or whose `TOOLS` attribute is missing/malformed; startup must still succeed with the remaining valid tools
  - Observable: with both example tools and a deliberately broken fixture module on the path, `MCP_SERVER` is built, `ALLOWED_TOOLS` contains exactly `mcp__local__echo` and `mcp__local__read_url`, and the broken module produces a logged error without raising
  - _Requirements: 4.1, 4.5_
  - _Boundary: app/tools/__init__.py_

- [ ] 5. Core: agent wrapper that translates SDK messages to SSE events
- [ ] 5.1 Implement the streaming turn function
  - Build `ClaudeAgentOptions` with `mcp_servers={"local": tools.MCP_SERVER}`, `allowed_tools=tools.ALLOWED_TOOLS`, `resume=session.sdk_session_id` when present, and `model` from settings when set
  - Iterate `query()`; for each `AssistantMessage` yield one `text` event per `TextBlock` and one `tool_call` event per `ToolUseBlock`; for each subsequent `UserMessage` containing tool-result blocks yield `tool_result`; on `ResultMessage` capture and persist `sdk_session_id` if absent, `touch_session`, and yield the terminal `done` event with `session_id`, `usage`, and `is_error`
  - Persist a corresponding row via the DB helpers as each event is yielded so the persisted history matches what was streamed even on disconnect
  - Catch any exception during iteration: append a `kind="error"` row, yield one `error` event, and exit; do not yield `done` after an error
  - Observable: a unit test with the SDK call monkey-patched to return a fixture stream produces the expected ordered sequence of SSE events and DB rows, and an error fixture produces an `error` event followed by stream close (no `done`)
  - _Requirements: 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.4_
  - _Depends: 2.3, 3.2, 4.2_

- [ ] 6. Core: browser SPA
- [ ] 6.1 (P) Build the static SPA shell and styling
  - Author `frontend/index.html` (sidebar, main pane, composer), `frontend/styles.css`, and the entry-point `frontend/app.js` skeleton with no third-party JS or analytics
  - Implement the in-memory token modal that captures the bearer token on first load and never persists it
  - Observable: opening `index.html` directly in a browser shows the layout and the token modal; submitting a value stores it in a module variable that survives navigation but is cleared on full reload
  - _Requirements: 5.1, 5.2, 5.7, 5.8, 1.4, 1.5_
  - _Boundary: frontend/_

- [ ] 6.2 Implement session list, history rendering, and the SSE consumer
  - On load and after each new chat, call `GET /api/sessions` with the bearer token and render the sidebar; clicking a session calls `GET /api/sessions/{id}/messages` and renders the history
  - Render user and assistant text as distinct bubbles; render `tool_call` and `tool_result` events as collapsible `<details>` blocks within the conversation
  - Submit user messages via `fetch` to `POST /api/sessions/{id}/messages`; consume the SSE response with a hand-rolled `\n\n`-splitting `ReadableStream` parser that matches the pinned wire framing; append `text` deltas to the in-progress assistant bubble; close the stream on `done`
  - Observable: with a stub backend serving recorded `text`/`tool_call`/`tool_result`/`done` frames, the page renders the assistant bubble incrementally, shows tool-call blocks as collapsibles, and stops appending after `done`
  - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 1.4_
  - _Boundary: frontend/_

- [ ] 7. Integration: FastAPI routes, session lock, and static mount
- [ ] 7.1 Wire the FastAPI app, lifespan startup, and static mount
  - Build the FastAPI app with a lifespan handler that loads settings, calls `init_db`, imports `app.tools` (triggering discovery), and stashes settings + a `session_locks: dict[str, asyncio.Lock]` map on `app.state`
  - Mount `frontend/` as static files so `GET /` serves `index.html` and asset paths resolve
  - Observable: starting the app via `poe dev` against a populated `.env` returns 200 on `GET /` with `index.html` content and logs successful tool discovery for `echo` and `read_url`
  - _Requirements: 5.1, 6.1_
  - _Depends: 2.1, 3.1, 4.2_

- [ ] 7.2 Implement the session API endpoints
  - `POST /api/sessions` creates a session and returns `{id, title, created_at, updated_at}`; `GET /api/sessions` returns the list ordered most-recently-updated first; `GET /api/sessions/{id}/messages` returns the full message history or 404 if the id is unknown
  - Apply the bearer-token dependency to every `/api/*` route
  - Observable: against a running app, an end-to-end test using `httpx.AsyncClient` exercises create → list → fetch-history and asserts a 404 for an unknown id and a 401 for a missing token
  - _Requirements: 1.3, 2.1, 2.2, 2.3, 2.4_
  - _Depends: 2.2, 3.2, 7.1_

- [ ] 7.3 Implement the streaming send-message endpoint with per-session lock
  - On `POST /api/sessions/{id}/messages`: enforce auth, return 404 if the session does not exist, persist the user message, then attempt to acquire `app.state.session_locks[id]` non-blocking; if held, return HTTP 409 with `{"error": "session_busy"}`
  - With the lock held, return an `EventSourceResponse` (or equivalent) that drives `agent.stream_turn`, polls `await request.is_disconnected()` between events, and on disconnect stops iterating without emitting `done`
  - Set `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`
  - Observable: an integration test with the SDK call monkey-patched verifies the response is `text/event-stream`, the SSE bytes contain `event: text` then `event: done`, the persisted history matches what was streamed, and a concurrent second POST for the same session id receives 409
  - _Requirements: 1.3, 2.4, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  - _Depends: 5.1, 7.2_

- [ ] 8. Integration: deployment artifacts and operator documentation
- [ ] 8.1 Author the Railway deployment artifacts
  - Add `nixpacks.toml` declaring both `python` and `nodejs` providers and an install step that runs `npm i -g @anthropic-ai/claude-code` so the SDK's CLI prerequisite is satisfied
  - Add a `Procfile` (or `railway.json`) whose start command is `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - Observable: `nixpacks plan .` (or equivalent dry run) shows both python and node phases plus the global CLI install, and the start command resolves the `$PORT` placeholder
  - _Requirements: 6.4, 6.5_

- [ ] 8.2 Write the README covering setup, run, deploy, lock-down, and tool authoring
  - Document local run via `uv run uvicorn app.main:app --reload`, Railway deploy via nixpacks auto-detection (with the env vars and the volume note), and the MVP access-control posture (default Railway URL + bearer token, custom-domain/Cloudflare Access deferred)
  - Document Tailscale Funnel as a self-hosted lock-down option (not a Railway one)
  - Document the Railway Volume attachment for `CONVERSATIONS_DB_PATH` so conversation history survives redeploys
  - Include a "How to add a new tool" section with a ~10-line minimal example demonstrating `@tool` plus `TOOLS = [...]`
  - Observable: the README contains all five sections and a reader can follow the local-run section end-to-end without consulting the design document
  - _Requirements: 4.6, 6.4, 6.5, 6.6, 6.7, 2.5_
  - _Depends: 4.2, 7.1, 8.1_

- [ ] 9. Validation: automated test suite
- [ ] 9.1 (P) Unit and integration tests for foundation modules
  - Cover `db.py` (schema init idempotency, CRUD round-trip, ordering), `auth.py` (401 + passthrough), and `events.py` (per-event-type framing round-trip)
  - Each test exercises behavior referenced in its requirement IDs
  - Observable: `poe test` runs these tests and they all pass against a fresh checkout
  - _Requirements: 1.3, 2.1, 2.2, 2.3, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5_
  - _Boundary: tests/test_db.py, tests/test_auth.py, tests/test_events.py_

- [ ] 9.2 (P) Tool discovery test with a deliberately broken fixture module
  - Place a fixture module that raises on import under a tests-only tools path; assert that discovery logs an error, skips it, and still registers `echo` and `read_url`
  - Observable: the test asserts the final `ALLOWED_TOOLS` contains exactly the two example tool names and the captured log records contain the broken module's name
  - _Requirements: 4.1, 4.2, 4.3, 4.5_
  - _Boundary: tests/test_tools_discovery.py_

- [ ] 9.3 Route and streaming integration tests with the SDK monkey-patched
  - Drive the FastAPI app with `httpx.AsyncClient`; cover session create/list, history fetch (including 404), unauthenticated 401, send-message happy path (asserts `event: text` … `event: done` ordering and persisted rows), error path (asserts `event: error` and no `done`), and concurrent-second-POST 409
  - Observable: `poe test` runs these tests and they all pass; the test that exercises 409 demonstrates the per-session lock works
  - _Requirements: 1.3, 1.6, 2.1, 2.2, 2.3, 2.4, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.4_
  - _Depends: 7.3, 9.1, 9.2_

- [ ] 10. Validation: end-to-end smoke
- [ ] 10.1 Run and document the manual end-to-end smoke
  - With real `ANTHROPIC_API_KEY` and the `claude` CLI installed, start the app via `poe demo`, open the SPA, create a session, send a prompt that forces the model to call the `echo` tool, and confirm the streamed sequence is `text` → `tool_call` → `tool_result` → `text` → `done`
  - Capture the steps in the README so the operator can reproduce the smoke after any change
  - Observable: a documented checklist in the README is executed once on a clean machine and passes; the resulting `conversations.db` contains the expected message rows for the smoke session
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.2, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_
  - _Depends: 7.3, 8.2_
