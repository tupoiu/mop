# Research & Design Decisions — claude-agent-webapp

## Summary
- **Feature**: `claude-agent-webapp`
- **Discovery Scope**: New Feature (greenfield Python service + browser SPA)
- **Key Findings**:
  - `claude-agent-sdk` (PyPI 0.1.80) shells out to the `claude` CLI under the hood; the host must have Node + `@anthropic-ai/claude-code` installed and discoverable.
  - The SDK registers custom Python tools **only via MCP** — but the `create_sdk_mcp_server()` in-process path is a single function call and needs no external server. We are not using `mcp_servers.json` (out of scope per requirements), but we do use the SDK's in-process MCP for custom tool wiring.
  - Multi-turn resumption is built into the SDK: capture `ResultMessage.session_id` after turn 1, then pass `ClaudeAgentOptions(resume=session_id)` on subsequent turns. We do not need to replay history ourselves.

## Research Log

### Topic: claude-agent-sdk Python API surface
- **Context**: Requirements mandate using the latest `claude-agent-sdk` and explicitly forbid guessing the API.
- **Sources Consulted**:
  - https://pypi.org/project/claude-agent-sdk/ (0.1.80)
  - https://github.com/anthropics/claude-agent-sdk-python (CHANGELOG)
  - https://code.claude.com/docs/en/agent-sdk/python
- **Findings**:
  - Two entry points: `query(prompt, options)` for one-shot, and `ClaudeSDKClient` for interactive sessions. Both yield an `AsyncIterator[Message]`.
  - Message union: `UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent | RateLimitEvent | TaskStarted/Progress/Notification`.
  - `AssistantMessage.content` is `list[ContentBlock]` — `TextBlock`, `ThinkingBlock`, `ToolUseBlock`. Tool results arrive as content blocks inside a subsequent `UserMessage`.
  - `ResultMessage` ends a turn and carries `session_id`, `total_cost_usd`, `usage`, `is_error`.
  - Token-delta streaming requires `ClaudeAgentOptions(include_partial_messages=True)`; deltas surface as `StreamEvent.event` carrying raw Claude API stream events.
  - Resume: `ClaudeAgentOptions(resume="<session_id>")`; first turn has no resume.
  - Custom tools: `@tool("name", "desc", schema_dict_or_json_schema)` on an `async def` returning `{"content":[{"type":"text","text":...}]}`. Bundled into `create_sdk_mcp_server(name, version, tools=[...])` and passed as `ClaudeAgentOptions(mcp_servers={"local": server}, allowed_tools=["mcp__local__<tool_name>"])`.
- **Implications**:
  - Our `POST /api/sessions/{id}/messages` handler creates a one-shot `query()` per turn with `resume=session_id` (after turn 1). Simpler than holding `ClaudeSDKClient` across HTTP requests.
  - Mapping SDK message stream → SSE: `TextBlock` (or `StreamEvent` deltas) → `text`; `ToolUseBlock` → `tool_call`; tool-result blocks in next `UserMessage` → `tool_result`; `ResultMessage` → `done` with `session_id` + `usage`.
  - Auto-discovered tools all funnel into one in-process MCP server named `local`; allowed-tools list is computed at startup.

### Topic: Railway + nixpacks compatibility for a CLI-backed Python app
- **Context**: Requirement R6 specifies Railway via nixpacks.
- **Sources Consulted**: claude-agent-sdk-python README ("Prerequisites" — Node 18+ and `claude` CLI); Railway nixpacks Python provider docs.
- **Findings**:
  - Nixpacks Python provider does not install Node by default. We need a `nixpacks.toml` declaring both `python` and `nodejs` with a phase that runs `npm i -g @anthropic-ai/claude-code`.
  - SQLite file on Railway's container FS is wiped on redeploy unless mounted on a Volume.
- **Implications**:
  - Add `nixpacks.toml` to the repo as a deploy artifact (not a source file the app reads).
  - Make the SQLite file path env-configurable (`CONVERSATIONS_DB_PATH`) so it can point at the volume mount.

### Topic: Frontend stack — vanilla vs React
- **Context**: Requirements R5 AC8 forbids "third-party JavaScript". Earlier conversation considered a React + Vite + Tailwind path inspired by siteboon/claudecodeui.
- **Findings**:
  - Strict reading of "no third-party JS" rules out vendoring marked.js / react-markdown / highlight.js.
  - For an MVP single-user prototype, plain `<pre>`-wrapped text plus simple code-fence detection is sufficient and keeps total LOC tiny.
- **Implications**:
  - MVP frontend: vanilla HTML + ES module JS, native `EventSource` for SSE, minimal CSS. No build step.
  - Deferred: switch to React + Vite + `react-markdown` later if rendering quality becomes the bottleneck.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Per-request `query()` with `resume=session_id` | Each POST opens a fresh SDK call; SDK handles history via session id | Stateless server, easy concurrency, restart-safe | Slight startup cost per turn (CLI spawn) | Selected |
| Long-lived `ClaudeSDKClient` per session | Hold SDK client object in-memory keyed by session id | Lower per-turn latency | In-process state lost on restart, complicates SSE cancellation, hard to reason about | Rejected |
| Persist full message history server-side, replay each turn | Send prior messages on every call | Independent of SDK session storage | SDK doesn't accept history list; would require workaround | Rejected — fights the SDK |

## Design Decisions

### Decision: One-shot `query()` per turn with SDK session resume
- **Context**: The SDK manages multi-turn state internally via `session_id`; we already need to persist messages for our own UI history.
- **Selected Approach**: First turn → `query(prompt, options)` with no resume; capture `session_id` from `ResultMessage` and store it on the session row. Subsequent turns → `query(prompt, ClaudeAgentOptions(resume=session_id, ...))`.
- **Rationale**: Stateless HTTP handlers, no in-process session map, restart-safe.
- **Trade-offs**: Each turn spawns a CLI subprocess (cold-start cost ~hundreds of ms). Acceptable for single-user.

### Decision: In-process SDK MCP server for custom tools
- **Context**: SDK only registers tools via MCP. External `mcp_servers.json` is out of scope.
- **Selected Approach**: At startup, scan `app/tools/`, collect every `@tool`-decorated async function, bundle them into one `create_sdk_mcp_server(name="local", version="0.1.0", tools=[...])`. Cache the server config and the `allowed_tools` list (`["mcp__local__<name>", ...]`) at module scope; reuse on every turn via `ClaudeAgentOptions(mcp_servers={"local": server}, allowed_tools=allowed)`.
- **Rationale**: Matches the SDK's only supported tool registration mechanism without exposing MCP as a user-facing concept.
- **Trade-offs**: A broken tool module fails at startup-time discovery; we log and skip it (R3 AC5).

### Decision: SQLite with single-writer serialization via `aiosqlite`
- **Context**: Single-user app, low write volume, durability required across restarts.
- **Selected Approach**: `aiosqlite` connection per request, `PRAGMA journal_mode=WAL`, two tables: `sessions`, `messages`. DB path from `CONVERSATIONS_DB_PATH` (default `./conversations.db`).
- **Rationale**: Simplest possible store; WAL handles single-writer + concurrent readers cleanly.
- **Trade-offs**: No schema migration tooling; we'll do `CREATE TABLE IF NOT EXISTS` on startup. Acceptable for prototype.

### Decision: Vanilla HTML/JS frontend, no build step
- **Context**: R5 AC8 forbids third-party JS; requirements value clarity over features.
- **Selected Approach**: `frontend/index.html` + `frontend/app.js` (ES module) + `frontend/styles.css`, served by FastAPI as static files. Native `fetch` + `EventSource`. Plain-text rendering with simple `<pre>`-wrapping for fenced code blocks.
- **Rationale**: Zero build tooling, zero JS deps, works offline-first.
- **Follow-up**: If markdown rendering quality is needed, switch to React + Vite + `react-markdown` and re-evaluate the "no third-party JS" rule.

## Risks & Mitigations
- **Claude Code CLI not present at runtime** — Document the prerequisite in README; declare both `python` and `nodejs` in `nixpacks.toml`; fail at startup with a clear message if `claude` is not on `PATH`.
- **SQLite wiped on Railway redeploy** — DB path is env-configurable; README walks through attaching a Volume and pointing `CONVERSATIONS_DB_PATH` at it.
- **`include_partial_messages=True` produces high-frequency events** — Coalesce token deltas into a single SSE `text` event per `AssistantMessage` block; we don't need per-token granularity for the MVP. (If the user prefers per-token, surface raw `StreamEvent` deltas instead.)
- **Tool import failure crashing startup** — Wrap each tool module import in try/except; log and skip on failure (R3 AC5).
- **Long-running `read_url` blocking the event loop** — Use `httpx.AsyncClient` with a short timeout; cap response body size.

## References
- [claude-agent-sdk on PyPI](https://pypi.org/project/claude-agent-sdk/) — package version, install
- [claude-agent-sdk-python GitHub](https://github.com/anthropics/claude-agent-sdk-python) — CHANGELOG, examples
- [Claude Agent SDK Python docs](https://code.claude.com/docs/en/agent-sdk/python) — API reference
- [FastAPI](https://fastapi.tiangolo.com/) — server framework
- [sse-starlette](https://github.com/sysid/sse-starlette) — SSE response helper
- [aiosqlite](https://github.com/omnilib/aiosqlite) — async SQLite driver
- [poethepoet](https://poethepoet.natn.io/) — task runner
