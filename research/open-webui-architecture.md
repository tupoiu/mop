# Open WebUI — Streaming Chat Architecture

Source: https://github.com/open-webui/open-webui (main branch, May 2026)

---

## Overview

Open WebUI is a full-featured, multi-user, multi-model chat platform built on FastAPI (backend) and Svelte (frontend). Its streaming chat architecture is notably more complex than a single-provider wrapper: it acts as an aggregating gateway that routes to Ollama, OpenAI-compatible, Anthropic, Azure, and custom pipeline backends, while managing real-time events through a parallel Socket.IO channel.

---

## Technology Stack

| Layer | Choice | Notes |
|---|---|---|
| Web framework | FastAPI + Starlette + uvicorn | ~25 routers registered via `include_router()` |
| Frontend | Svelte + TypeScript + Vite | Rich SPA; separate build step |
| Real-time transport | Socket.IO (python-socketio) mounted at `/ws` | Primary streaming path to browser |
| HTTP streaming | `StreamingResponse` (Starlette) | Used for direct/legacy API calls |
| Upstream HTTP client | aiohttp | Proxies requests to LLM backends |
| Database | SQLAlchemy async + aiosqlite (SQLite) or PostgreSQL | Async sessions via `get_async_session` |
| Distributed state | Redis | Session pool, usage/load tracking, multi-instance lock |
| Background tasks | asyncio tasks + Redis-backed scheduler | Title/tag generation, cleanup |
| Middleware | Pure ASGI classes (not `BaseHTTPMiddleware`) | Avoids SQLAlchemy cancellation issues |
| Telemetry | OpenTelemetry (optional) | |
| Schema generation | Pydantic v2 + custom OpenAI schema converter | Used for tool specs |

---

## Request Entry Point

```
POST /api/chat/completions   (main.py)
```

This is the primary chat handler. For requests tied to a chat session it **does not stream directly to the HTTP response**. Instead it:

1. Validates the model and user access.
2. Fans out across multiple selected models, creating one background `asyncio.Task` per model.
3. Returns task identifiers and the chat ID immediately.
4. Each background task calls `process_chat()`, which delegates to `generate_chat_completion()` in `utils/chat.py`.

For direct/legacy API calls (no chat session context) it runs synchronously through `generate_direct_chat_completion()`, which does stream over the HTTP connection using an asyncio.Queue + Socket.IO listener pattern.

---

## Dual-Channel Streaming Architecture

Open WebUI operates two parallel real-time channels:

### Channel 1: Socket.IO (primary UI path)

The background task processing each model invokes `get_event_emitter()`, which returns an async callable that emits to `room=f'user:{user_id}'` via Socket.IO. The Svelte frontend listens on this socket and receives:

- `events` — individual message content updates (token-by-token at ≤6 Hz via a 150 ms throttle interval)
- `chat:active`, `chat:message:error`, `chat:tasks:cancel` — lifecycle events
- `events:channel` — channel messages with typing indicators

This channel allows the backend to push updates asynchronously without the browser holding an open HTTP response.

### Channel 2: SSE / StreamingResponse (direct/API path)

When clients call the endpoint without a chat session context (or for direct API proxy calls), the response is a Starlette `StreamingResponse` with `Content-Type: text/event-stream`. The `stream_wrapper()` utility:

- Prepends selected-model metadata to the SSE output.
- Strips `Content-Encoding`, `Content-Length`, and `Transfer-Encoding` headers (aiohttp already decompresses; double-decoding breaks clients).
- For Ollama responses, converts payload format: `convert_payload_openai_to_ollama` on the way in, `convert_streaming_response_ollama_to_openai` on the way out.
- For Anthropic responses, converts Anthropic Messages API format → OpenAI Chat Completions format internally before streaming.

---

## Backend Routing Inside `generate_chat_completion`

```
model type?
  ├── pipe model      → generate_function_chat_completion()   (custom Python pipe)
  ├── ollama          → generate_ollama_chat_completion()      (format convert + proxy)
  ├── openai-compat   → generate_openai_chat_completion()      (proxy via aiohttp)
  └── arena           → random sub-model selection, then recurse
```

All paths apply inlet and outlet **pipeline filters** (see below) around the actual completion call.

---

## Tool Use

### Tool Sources (three kinds)

1. **Built-in tools** — web search, code execution, memory management. Conditionally injected by `get_builtin_tools()` based on feature flags and model capability metadata.
2. **Local tools** — user-authored Python functions stored in the database. Loaded by `get_tools()` with per-user access control via `AccessGrants`.
3. **External/remote tools** — OpenAPI-specified servers and terminal servers. Called via `execute_tool_server()` which parses the OpenAPI spec, routes path/query/body parameters, and executes the HTTP request with authentication (bearer, session, OAuth).

### Tool Registration Flow

Each tool's Python function is converted to an OpenAI-compatible schema:

```
function signature
  → parse docstring + type hints
  → convert_function_to_pydantic_model()
  → convert_pydantic_model_to_openai_function_spec()
  → clean_openai_tool_schema()     (resolve anyOf, remove nulls)
```

Infrastructure parameters (prefixed `__`, e.g. `__user__`, `__event_emitter__`, `__metadata__`) are stripped from the schema before it is sent to the LLM so the model never sees them.

### MCP (Model Context Protocol) Support

MCP clients are managed per-request and explicitly disconnected in the `finally` block of `process_chat()`, preventing resource leaks across turns.

### Tool Invocation

`get_async_tool_function_and_apply_extra_params()` wraps each tool callable to:
- Inject execution context (`Request`, `UserModel`, `EventEmitter`, chat/message IDs).
- Present a clean async interface to the calling layer.

The result is a `tools_dict` mapping function name → `{callable, spec, metadata}`.

### Agentic Loop

Open WebUI **does not implement its own multi-turn tool-use loop**. It sends the tool specs to the upstream LLM (OpenAI, Ollama, etc.) as part of the standard `tools` parameter. The LLM decides when to call a tool, returns a response with `tool_calls`, and the client/upstream manages re-submission. For pipeline (`pipe`) models, the custom Python pipe function itself manages any looping.

---

## Pipeline / Filter System

Both inlet (pre-completion) and outlet (post-completion) filter hooks are applied via `process_pipeline_inlet_filter()` and `process_pipeline_outlet_filter()`. Filters receive rich context:

```python
{
    '__event_emitter__': ...,    # Socket.IO push
    '__event_call__': ...,       # awaitable for sync-to-async bridge
    '__user__': UserModel,
    '__metadata__': {chat_id, message_id, folder_knowledge},
    '__request__': Request,
    '__model__': model_object,
}
```

Filters are sorted and applied in sequence; they can modify the payload or emit events mid-turn.

---

## Middleware Stack

Applied in order (pure ASGI, not `BaseHTTPMiddleware`):

1. `RedirectMiddleware`
2. `SecurityHeadersMiddleware`
3. `CommitSessionMiddleware`
4. `AuthTokenMiddleware` (JWT + trusted-header validation)
5. `WebsocketUpgradeGuardMiddleware`
6. `CORSMiddleware` (all origins/methods/headers)
7. `CompressMiddleware` (Brotli/gzip, optional)
8. `AuditLoggingMiddleware` (optional)
9. `StarSessionsMiddleware` (Redis-backed sessions, optional)

The choice of pure ASGI over `BaseHTTPMiddleware` is explicit: the latter caused "noisy SQLAlchemy terminate_force_close tracebacks" from anyio task group cancellation.

---

## Concurrency and Multi-Instance

- **Per-request**: background asyncio tasks, one per model in a fanout.
- **Per-cluster**: Redis `SESSION_POOL` and `USAGE_POOL` with periodic cleanup and distributed locks allow multiple uvicorn instances to share state.
- **Rate-limiting on socket events**: 150 ms throttle on token-by-token socket emissions.

---

## Data Model (Chat Persistence)

SQLAlchemy async models, migrated via Alembic. Each chat and message is persisted to the database *before* Socket.IO emission, ensuring DB and socket state are consistent. The DB supports both SQLite (aiosqlite) and PostgreSQL.

---

## Summary: Key Architectural Choices

- **Socket.IO as the primary streaming channel**, not raw SSE — enables multi-tab consistency and server-initiated push.
- **Gateway/proxy pattern** — Open WebUI never runs inference itself; it routes to pluggable backends.
- **No built-in agentic tool-use loop** — tool specs are passed to the upstream LLM; the LLM drives tool calls.
- **Pipeline filter system** — middleware-style hooks around every completion, enabling extensible pre/post-processing.
- **Multi-model fanout** — a single chat turn can spawn parallel completions across models.
- **Redis for horizontal scaling** — session and usage state shared across instances.
