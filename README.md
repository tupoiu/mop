# Claude Agent Webapp

A minimal, self-hosted web application that exposes a Claude AI agent with a streaming chat UI.
The server runs on FastAPI, persists conversation history in SQLite, and integrates with the
Claude Code CLI so tools can be added without touching the server itself.

---

## Table of Contents

1. [Local Run](#local-run)
2. [Railway Deploy](#railway-deploy)
3. [Access Control / Lock-down](#access-control--lock-down)
4. [Environment Variables](#environment-variables)
5. [How to Add a New Tool](#how-to-add-a-new-tool)

---

## Local Run

### Prerequisites

| Tool | Minimum version | Install reference |
|------|----------------|-------------------|
| Python | 3.11 | <https://www.python.org/downloads/> |
| Node.js | 18 | <https://nodejs.org/> |
| uv | latest | `pip install uv` or <https://docs.astral.sh/uv/getting-started/installation/> |
| Claude Code CLI | latest | `npm install -g @anthropic-ai/claude-code` |

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd claude-agent-webapp
uv sync --group dev
```

### 2. Configure environment variables

Copy the example env file and fill in the required values:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
APP_AUTH_TOKEN=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
ANTHROPIC_API_KEY=<your Anthropic API key>
```

`CONVERSATIONS_DB_PATH` defaults to `./conversations.db` in the project root, which is fine
for local development.

### 3. Start the server

```bash
uv run uvicorn app.main:app --reload
```

Or, using the `poe` task runner (loads `.env` automatically and binds to `127.0.0.1:8000`):

```bash
uv run poe dev
```

The app is now available at <http://127.0.0.1:8000>.

Every API request must include the bearer token:

```
Authorization: Bearer <APP_AUTH_TOKEN>
```

The web UI sends this automatically once you enter the token in the settings panel.

### 4. Development helpers

Run `poe --help` to see all available commands.

---

## Railway Deploy

Railway auto-detects `nixpacks.toml` and installs both Python 3.11 and Node.js 20, then runs
the command in `Procfile` (`uvicorn app.main:app --host 0.0.0.0 --port $PORT`). No Dockerfile
is needed.

### 1. Create a new Railway project

```bash
# Install the Railway CLI if you haven't already
npm install -g @railway/cli
railway login
railway init
```

Or create the project from <https://railway.app> and connect your repository.

### 2. Set required environment variables

In the Railway dashboard go to **Variables** and add:

| Variable | Notes |
|----------|-------|
| `APP_AUTH_TOKEN` | Required. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ANTHROPIC_API_KEY` | Required. Your Anthropic API key. Never committed to the repo. |
| `CONVERSATIONS_DB_PATH` | Set this **after** attaching a volume (see next step). |
| `ANTHROPIC_MODEL` | Optional. Defaults to the SDK's built-in model. |

### 3. Attach a Railway Volume for the database

Conversation history is stored in the file pointed to by `CONVERSATIONS_DB_PATH`.
Without a persistent volume, this file is lost on every redeploy.

1. In the Railway dashboard, open your service and click **Add Volume**.
2. Choose a mount path, for example `/data`.
3. Set the environment variable: `CONVERSATIONS_DB_PATH=/data/conversations.db`

Railway will keep the volume across deploys and restarts, so conversation history survives.

### 4. Deploy

Push to the branch connected to Railway, or trigger a manual deploy from the dashboard.
Railway will run `nixpacks build` automatically.

---

## Access Control / Lock-down

### MVP posture (Railway deployment)

In the current MVP, the application is reachable on its default Railway-provided URL
(for example `https://your-app.up.railway.app`).

**The bearer token (`APP_AUTH_TOKEN`) is the only access-control mechanism.**
Every request to `/api/*` is rejected with `401 Unauthorized` unless the correct token is
supplied in the `Authorization: Bearer` header. Keep the token secret and rotate it if it is
ever exposed.

Custom-domain setup and Cloudflare Access lock-down are deferred to a follow-up feature.

For self-hosted deployments, consider using [Tailscale](https://tailscale.com/kb/1223/funnel/) to expose the server securely without opening firewall ports.

---

## Environment Variables

All variables can be placed in a `.env` file (copy from `.env.example`). In production
they are set in the Railway Variables panel.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_AUTH_TOKEN` | Yes | — | Shared bearer token required on every `/api/*` request. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key. Server-side only; never sent to the browser. |
| `CONVERSATIONS_DB_PATH` | No | `./conversations.db` | Path to the SQLite conversation store. Override to point at a mounted volume in hosted environments. |
| `ANTHROPIC_MODEL` | No | SDK default | Claude model identifier. Leave unset to use the SDK default (e.g. `claude-sonnet-4-6`). |

---

## How to Add a New Tool

Tools live in `app/tools/`. Any Python module placed in that directory that exports a
`TOOLS` list is picked up automatically at startup — no server restart configuration needed
beyond restarting the process.

Here is a minimal example. Create `app/tools/greet.py`:

```python
from typing import Any
from pydantic import BaseModel
from claude_agent_sdk import SdkMcpTool
from app.tools._pydantic_tool import TextContent, ToolResult, pydantic_tool

class GreetArgs(BaseModel):
    name: str

@pydantic_tool("greet", "Return a greeting for the given name.", GreetArgs)
async def greet(args: GreetArgs) -> ToolResult:
    return ToolResult(content=[TextContent(text=f"Hello, {args.name}!")])

TOOLS: list[SdkMcpTool[Any]] = [greet]
```

That's all. The next time the server starts, `greet` will appear in the tool list available
to the Claude agent.

**Key points:**

- `@pydantic_tool(name, description, ArgsModel)` wraps your async handler and registers it
  with the MCP server that the Claude Code CLI connects to.
- The `ArgsModel` is a Pydantic `BaseModel` — add fields, validators, and defaults as needed.
- Return a `ToolResult` containing one or more `TextContent` items.
- Add the wrapped function to `TOOLS`; the auto-discovery in `app/tools/__init__.py` does
  the rest.

See `app/tools/echo.py` for a trivial example and `app/tools/read_url.py` for a tool that
makes an outbound HTTP request.
