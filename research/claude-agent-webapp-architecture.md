# Claude Agent Webapp — Streaming Chat Architecture

Source: `/workspace` — spec at `.kiro/specs/claude-agent-webapp/`, implementation in `app/`.

---

## Overview

This project is a minimal, single-user, self-hosted web application that wraps the `claude-agent-sdk` Python package and exposes it through a browser chat UI. The architecture is deliberately thin: ~1 KLOC of application code across six Python modules and three frontend files. There is no abstraction for multi-model routing, no persistent auth system, and no distributed infrastructure — the design goal is a private agent surface the operator can understand and extend end-to-end.

---

## Technology Stack

| Layer | Choice | Notes |
|---|---|---|
| Web framework | FastAPI ≥0.110 + uvicorn + sse-starlette | Single process |
| Frontend | Vanilla HTML5 + ES module JS (no build step) | No third-party JS, no analytics |
| Agent runtime | `claude-agent-sdk` ≥0.1.80 | Shells out to `claude` CLI Node subprocess |
| SSE transport | sse-starlette `EventSourceResponse` | Pure SSE; no WebSocket |
| Database | aiosqlite (SQLite, WAL mode) | Single file; path from env var |
| HTTP client (tool) | httpx | Used only inside `read_url` tool |
| Schema/validation | Pydantic v2 + TypedDict | Strict mypy on `app/` |
| Task runner | poethepoet | `poe dev`, `poe test`, etc. |
| Type checker | mypy (strict) | |
| Packaging | pyproject.toml + uv | |
| Deployment target | Railway (nixpacks auto-detect) | nixpacks.toml declares Python + Node |

---

## Request Entry Point

```
POST /api/sessions/{session_id}/messages   (main.py)
```

This is the only non-trivial endpoint. It:

1. Verifies the bearer token (`require_token` dependency via `secrets.compare_digest`).
2. Loads the session from SQLite (404 if missing).
3. Persists the user message.
4. Acquires the per-session `asyncio.Lock` non-blocking; returns **HTTP 409** if the session is already mid-turn.
5. Returns an SSE `EventSourceResponse` that drives `agent.stream_turn()`.
6. Polls `await request.is_disconnected()` between SSE events; releases the lock and exits cleanly on client disconnect.

All `/api/*` routes are registered on a single `APIRouter` with `dependencies=[Depends(require_token)]`. The static mount at `/` (serving `frontend/`) is unauthenticated — the SPA itself prompts for the token before any API call.

---

## Streaming Architecture: Pure SSE

There is one transport channel: Server-Sent Events over the HTTP response body.

**Wire framing** (defined in `events.py`, pinned as a contract):

```
event: <type>\ndata: <json>\n\n
```

- No `id:`, no `retry:`, no comment lines.
- JSON `data` is a single line; inner newlines are JSON-escaped.
- The terminal event on success is always `done`; on error, a single `error` event is emitted and the stream closes without `done`.

**Browser SSE consumer**: native `EventSource` cannot send an `Authorization` header, so the frontend uses `fetch()` + a hand-rolled `ReadableStream` / `\n\n`-splitting parser (~30 lines in `app.js`). This satisfies the requirement that every API call carries the bearer token.

**Event vocabulary**:

| `event:` | `data:` JSON fields |
|---|---|
| `text` | `{text, message_ord}` |
| `tool_call` | `{id, name, input, message_ord}` |
| `tool_result` | `{tool_use_id, output, is_error, message_ord}` |
| `done` | `{session_id, usage, is_error}` |
| `error` | `{message}` |

---

## Agent Layer (`agent.py`)

`stream_turn()` is the core coroutine. It:

1. Builds `ClaudeAgentOptions`:
   - `mcp_servers={"local": tools.MCP_SERVER}` — the in-process tool server
   - `allowed_tools=tools.ALLOWED_TOOLS` — list of `mcp__local__<name>` strings
   - `resume=session.sdk_session_id` — session continuation (None on first turn)
   - `model=settings.anthropic_model` — optional model override from env
2. Calls `claude_agent_sdk.query(prompt=user_content, options=options)`, which is an async generator that yields typed message objects.
3. Translates each SDK message type to an SSE event and simultaneously persists a DB row:

```
SDK message type        → SSE event      → DB kind
─────────────────────────────────────────────────────
AssistantMessage
  TextBlock             → TextEvent      → "text"
  ToolUseBlock          → ToolCallEvent  → "tool_call"
UserMessage
  ToolResultBlock       → ToolResultEvent→ "tool_result"
ResultMessage           → DoneEvent      → (session update)
Exception               → ErrorEvent     → "error"
```

Persistence happens **as each event is yielded**, so the DB matches what the operator saw even if the connection drops mid-stream.

On `ResultMessage`:
- First turn: persists `sdk_session_id` from the SDK result (enables `resume=` on subsequent turns).
- Later turns: calls `touch_session()` to bump `updated_at`.

---

## Tool Use

### How Tools Are Registered

Tool registration is entirely in-process via the SDK's MCP mechanism:

```
app startup
  → import app.tools          (triggers __init__.py)
  → pkgutil.iter_modules()    scans app/tools/ package
  → importlib.import_module() for each submodule
  → collect TOOLS list from each module
  → create_sdk_mcp_server(name="local", tools=...)
  → ALLOWED_TOOLS = ["mcp__local__echo", "mcp__local__read_url", ...]
```

The `MCP_SERVER` and `ALLOWED_TOOLS` module-level constants are built **once at import time** and reused on every turn. There is no dynamic reload without a process restart.

### Tool Module Contract

Each file under `app/tools/` must export a module-level `TOOLS: list[SdkMcpTool]`. Modules without `TOOLS` are ignored (treated as helpers). Modules that fail to import are logged and skipped; startup continues with the remaining valid tools.

### Tool Definition Pattern

Tools are defined using a `pydantic_tool` decorator (`_pydantic_tool.py`) that wraps the SDK's `@sdk_tool`:

```python
class EchoArgs(BaseModel):
    text: str

@pydantic_tool("echo", "Return the input string unchanged.", EchoArgs)
async def echo(args: EchoArgs) -> ToolResult:
    return ToolResult(content=[TextContent(text=args.text)])

TOOLS: list[SdkMcpTool[Any]] = [echo]
```

The decorator:
1. Extracts the JSON schema from the Pydantic model.
2. Wraps the handler so the SDK receives `dict[str, Any]` and the handler receives a validated Pydantic model.

### Bundled Tools

- **`echo`** — returns input unchanged; serves as the minimal working example.
- **`read_url`** — fetches a URL via httpx (async, 10 s timeout, 200 KB cap, follows redirects).

### Agentic Loop

The SDK **manages the multi-turn tool-use loop internally**. The `claude` CLI subprocess handles:
- Sending the prompt + tool specs to the Anthropic API.
- Receiving `tool_calls` in the model response.
- Invoking the registered MCP tool (via the in-process MCP server).
- Appending the tool result to the conversation.
- Re-submitting to the model.
- Repeating until the model produces a final `ResultMessage`.

`agent.py` sees this as a linear stream of typed message objects; it does not implement or observe the loop structure — it just translates whatever the SDK yields into SSE events.

---

## Persistence (`db.py`)

SQLite via aiosqlite; WAL mode for concurrent reads during a streaming turn.

**Schema**:

```sql
sessions  (id TEXT PK, title TEXT, sdk_session_id TEXT, created_at TEXT, updated_at TEXT)
messages  (ord INTEGER PK AUTOINCREMENT, session_id FK, role, kind, content_json, created_at)
```

`content_json` shape varies by `kind`: `text`, `tool_call`, `tool_result`, `error`. No migrations layer — schema is applied idempotently at startup via `executescript`.

Each DB helper opens its own short-lived connection (`async with aiosqlite.connect(path)`); there is no connection pool. The `@asynccontextmanager` wrapper is critical here — `aiosqlite.Connection` is a `Thread` subclass and cannot be started twice.

---

## Authentication

Single shared bearer token read from `APP_AUTH_TOKEN` at startup. Compared using `secrets.compare_digest` (constant-time) against the `Authorization: Bearer <token>` header. If `APP_AUTH_TOKEN` or `ANTHROPIC_API_KEY` is missing/empty at startup, the app raises `RuntimeError` and uvicorn exits non-zero.

The token is held only in JS in-memory state on the frontend page session — never in localStorage, sessionStorage, or cookies.

---

## Concurrency

- One `asyncio.Lock` per session, stored in `app.state.session_locks`.
- Locks are lazily created (first access per session).
- A second concurrent `POST` for the same session returns **HTTP 409 Conflict** rather than queuing — keeps semantics obvious.
- SQLite WAL allows concurrent reads (e.g. sidebar refresh) without blocking the writer during a streaming turn.

---

## Frontend

Vanilla JS (ES modules), no build step, no third-party scripts, served from the same origin as the API via FastAPI's `StaticFiles` mount. Key implementation notes:

- Token modal on first load; token stored in a module-level variable only.
- Session list from `GET /api/sessions`; history from `GET /api/sessions/{id}/messages`.
- `tool_call` and `tool_result` events rendered as `<details>` collapsible blocks.
- SSE consumed via `fetch()` + `ReadableStream` (not `EventSource`) to allow the `Authorization` header.

---

## Module Dependency Graph

```
tools/*  →  agent.py  →  main.py
db.py               →  main.py
auth.py             →  main.py
config.py           →  main.py, agent.py, auth.py
events.py           →  agent.py, main.py
```

`tools/*` does not import `agent.py` or `main.py`. `agent.py` does not import `main.py`. Strictly layered.

---

## Summary: Key Architectural Choices

- **Pure SSE, no WebSocket** — simpler client implementation; fetch+ReadableStream needed only to add the auth header.
- **SDK-managed agentic loop** — `agent.py` is a translator, not an orchestrator; the `claude` CLI subprocess drives the tool-use cycle.
- **In-process MCP server** — tools are registered as a local MCP server inside the Python process; the SDK calls them via its internal protocol.
- **Auto-discovery at import time** — adding a tool is a one-file change with no edits to routing or agent code.
- **Per-session asyncio.Lock** — simple, explicit, single-process concurrency; no queue, no 409-retry.
- **SQLite only** — appropriate for a single-user self-hosted app; no migration layer needed yet.
- **Zero frontend dependencies** — no build toolchain, no npm, no third-party JS loaded at runtime.
