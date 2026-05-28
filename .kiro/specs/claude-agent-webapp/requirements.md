# Requirements Document

## Introduction
This feature delivers a minimal self-hosted single-user web application that wraps the official `claude-agent-sdk` Python package and exposes it through a browser UI. The owner-operator runs the app on their own infrastructure, authenticates with a single shared bearer token, and chats with a Claude agent that can stream responses, invoke custom Python tools, and use MCP servers. Conversations persist locally in SQLite so the operator can resume past sessions. The primary value is a private, low-friction agent surface that the operator can extend by dropping new tool files into a directory.

## Boundary Context
- **In scope**: single-user authentication via shared bearer token; session creation, listing, history retrieval, and resumption; streaming agent responses (text deltas, tool calls, tool results, completion) over SSE; auto-discovery of Python tools from a `tools/` directory; serving the chat UI from the same origin as the API.
- **Out of scope**: multi-user accounts, role-based access, password-based or SSO login, organization/team features; cloud-hosted conversation storage; horizontal scaling, multi-instance coordination, background job workers; built-in analytics, telemetry, or usage metrics; mobile-native clients; in-app tool authoring or hot-reload of tools without restart; MCP server integration; custom-domain setup and Cloudflare Access lock-down (deferred follow-up).
- **Adjacent expectations**: the operator provides a valid Anthropic API key via environment variable; the operator manages network exposure and TLS termination (e.g., via Cloudflare Access, Tailscale Funnel, or a reverse proxy); the host filesystem is trusted (the SQLite file and tool modules live on it).

## Requirements

### Requirement 1: Single-User Bearer Token Authentication
**Objective:** As the operator, I want every API request to require a shared bearer token, so that only I can use the deployed app.

#### Acceptance Criteria
1. The Agent Web App shall read the expected bearer token from the `APP_AUTH_TOKEN` environment variable at startup.
2. If `APP_AUTH_TOKEN` is unset or empty at startup, then the Agent Web App shall refuse to start and emit an error explaining the missing token.
3. When an API request arrives without an `Authorization: Bearer <token>` header that matches the configured token, the Agent Web App shall reject the request with HTTP 401 and not invoke any agent or tool.
4. When the frontend loads for the first time and has no token in memory, the Agent Web App frontend shall prompt the operator for the token before issuing any API request.
5. The Agent Web App frontend shall hold the token only in in-memory state for the page session and shall not persist it to local storage, cookies, or any other browser storage.
6. The Agent Web App shall never include the Anthropic API key in any response body, header, log line, or SSE event delivered to the client.

### Requirement 2: Session Lifecycle and History
**Objective:** As the operator, I want to create, list, and resume chat sessions, so that I can pick up past conversations and keep work organized.

#### Acceptance Criteria
1. When the operator submits an authenticated `POST /api/sessions` request, the Agent Web App shall create a new session with a unique session id and return that id in the response.
2. When the operator submits an authenticated `GET /api/sessions` request, the Agent Web App shall return the list of existing sessions ordered from most recently updated to least recently updated.
3. When the operator submits an authenticated `GET /api/sessions/{id}/messages` request for an existing session, the Agent Web App shall return the full ordered message history for that session, including user messages, assistant text, tool calls, and tool results.
4. If the operator requests messages for a session id that does not exist, then the Agent Web App shall respond with HTTP 404.
5. The Agent Web App shall persist all sessions and messages to a local SQLite database file so that history survives process restarts, and the file path shall be configurable via an environment variable so the database can live on a mounted volume in hosted environments.
6. When the operator sends a new message to an existing session, the Agent Web App shall include the prior session history as context for the agent so the conversation resumes coherently.

### Requirement 3: Streaming Agent Responses
**Objective:** As the operator, I want assistant output to appear incrementally as it is produced, so that I get fast feedback and can watch tool activity in real time.

#### Acceptance Criteria
1. When the operator submits an authenticated `POST /api/sessions/{id}/messages` request with a JSON body containing `content`, the Agent Web App shall persist the user message and begin a Server-Sent Events response stream for that turn.
2. While the agent is generating output, the Agent Web App shall emit SSE events for assistant text deltas as they arrive from the SDK.
3. When the agent invokes a tool, the Agent Web App shall emit an SSE event describing the tool call (tool name and input arguments) before the tool runs.
4. When a tool call completes, the Agent Web App shall emit an SSE event containing the tool result associated with that call.
5. When the agent finishes the turn, the Agent Web App shall emit a terminal `done` SSE event and close the stream.
6. The Agent Web App shall persist the assistant text, tool calls, and tool results produced during the turn to the session's message history before or as the `done` event is emitted, so that a subsequent history fetch reflects the same content the operator just saw.
7. If the agent or a tool raises an error mid-stream, then the Agent Web App shall emit an SSE error event describing the failure and close the stream without leaving the session in a partially-saved state that contradicts what was streamed.

### Requirement 4: Custom Tool Auto-Discovery
**Objective:** As the operator, I want to add a new tool by dropping a Python file into the `tools/` directory, so that extending the agent is friction-free.

#### Acceptance Criteria
1. At startup, the Agent Web App shall scan the `app/tools/` directory and register every tool exported by the modules it finds, without requiring per-tool edits to routing or agent code.
2. The Agent Web App shall ship with an `echo` example tool that returns its input unchanged.
3. The Agent Web App shall ship with a `read_url` example tool that fetches a URL over HTTP and returns the response body as text.
4. When the agent invokes a registered custom tool during a turn, the Agent Web App shall execute that tool and surface its result through the same SSE tool-call/tool-result events used for built-in behavior.
5. If a module in `app/tools/` fails to import or export a valid tool at startup, then the Agent Web App shall log a clear error identifying the offending file and continue starting up with the remaining valid tools.
6. The Agent Web App documentation (README) shall include a "How to add a new tool" section with a minimal example of approximately ten lines.

### Requirement 5: Browser Chat Interface
**Objective:** As the operator, I want a simple browser UI to chat with the agent, so that I can use the app from any device with a browser.

#### Acceptance Criteria
1. When the operator opens `GET /` in a browser, the Agent Web App shall serve the single-page chat frontend from the same origin as the API.
2. The Agent Web App frontend shall display a sidebar listing existing sessions and a "New chat" control that creates a new session via the API.
3. When the operator selects a session in the sidebar, the Agent Web App frontend shall load and display that session's full message history.
4. The Agent Web App frontend shall render user messages and assistant messages as visually distinct bubbles in the main conversation pane.
5. The Agent Web App frontend shall render tool calls and their results as collapsible blocks within the conversation flow so they can be inspected or hidden.
6. While an assistant turn is streaming, the Agent Web App frontend shall append text deltas to the in-progress assistant bubble as they arrive over SSE.
7. The Agent Web App frontend shall provide an input control at the bottom of the conversation pane for submitting the next user message to the active session.
8. The Agent Web App frontend shall load no third-party JavaScript and shall emit no analytics or telemetry requests.

### Requirement 6: Configuration, Deployment, and Operability
**Objective:** As the operator, I want clear configuration and a documented run/deploy path, so that I can stand the app up locally or on a small host with minimal effort.

#### Acceptance Criteria
1. The Agent Web App shall read the Anthropic API key from the `ANTHROPIC_API_KEY` environment variable on the server side only.
2. If `ANTHROPIC_API_KEY` is unset or empty at startup, then the Agent Web App shall refuse to start and emit an error explaining the missing key.
3. The Agent Web App repository shall include a `.env.example` file enumerating every environment variable the app reads.
4. The Agent Web App repository shall include README instructions for running locally with `uv run uvicorn app.main:app --reload` and for deploying to Railway (via nixpacks auto-detection) with the required environment variables set.
5. The Agent Web App repository shall include a `Procfile` (or equivalent `railway.json`) with a start command that runs `uvicorn app.main:app` bound to host `0.0.0.0` and the `$PORT` environment variable provided by Railway.
6. The Agent Web App README shall document attaching a Railway Volume for the SQLite database file path so conversation history is not lost on redeploy.
7. The Agent Web App README shall note that for the MVP the deployment is reachable on its default Railway-provided URL and the bearer token is the sole access control, and shall mention Tailscale Funnel only as an option for the local/self-hosted path where Tailscale is installed on the host. Custom-domain setup and Cloudflare Access lock-down are deferred to a follow-up feature.

### Requirement 7: Developer Task Runner (poe)
**Objective:** As the operator, I want common dev and demo commands exposed as `poe` tasks, so that I can run, test, and demo the app without remembering long invocations.

#### Acceptance Criteria
1. The Agent Web App repository shall declare `poethepoet` as a development dependency and configure tasks under `[tool.poe.tasks]` in `pyproject.toml`.
2. The Agent Web App repository shall provide a `poe dev` task that starts the FastAPI server with auto-reload bound to a local port.
3. The Agent Web App repository shall provide a `poe demo` task that starts the server with a deterministic demo configuration (for example, a fixed `APP_AUTH_TOKEN` from `.env` and any seeded sample data) suitable for showing the app to someone.
4. The Agent Web App repository shall provide a `poe test` task that runs the project's test suite.
5. The Agent Web App repository shall provide a `poe lint` task and a `poe format` task that run the project's configured linter and formatter respectively.
6. The Agent Web App repository shall provide a `poe db-reset` task that deletes the local SQLite conversation database file so the operator can start from a clean state.
7. Each `poe` task definition in `pyproject.toml` shall include a `help` description so `poe --help` lists the available tasks with a short explanation of each.
