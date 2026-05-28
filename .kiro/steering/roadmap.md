---
inclusion: always
---

# Future Roadmap & Potential Improvements

Ideas and enhancements to consider for future development. These are not committed plans — they inform design decisions and should be evaluated when relevant work is being specced.

## Backend / AI

- **Model router**: Switch to a model router that supports tool use, allowing flexibility to swap or load-balance models while retaining function calling capability.
- **[low priority] SDK subprocess cleanup on disconnect**: Verify that abandoning the `stream_turn` async generator (on client disconnect) causes the SDK to terminate the underlying `claude` CLI Node subprocess. If not, add an explicit `finally: await gen.aclose()` in the SSE generator in `main.py`. Prevents orphaned subprocesses accumulating in production. See `research/learning-from-open-webui.md` win 4.
- **[low priority] Auto-generated session titles**: After the first turn's `done` event, fire a background `asyncio.create_task()` that calls the Anthropic API directly via httpx to generate a 4–6 word title and writes it to the DB via a new `update_session_title` helper in `db.py`. The sidebar currently shows untitled sessions. See `research/learning-from-open-webui.md` win 5.
- **[low priority] Structured turn logging**: After each `ResultMessage` in `agent.py`, emit a single `logger.info(...)` line capturing `session_id`, `sdk_session_id`, `is_error`, and `usage`. Zero-cost observability for debugging cost and error patterns in production. See `research/learning-from-open-webui.md` win 6.

## Frontend

- **Component library**: Evaluate adopting a component library (e.g., shadcn/ui, Radix UI, or similar) for the frontend to improve UI consistency, accessibility, and development speed.
