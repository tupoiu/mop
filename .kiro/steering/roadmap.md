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
- **TypeScript frontend**: Convert the plain-JS frontend to TypeScript for type safety across the SSE event shapes, API response types, and module boundaries. Svelte may be a good fit — its compiler-based approach avoids a heavy runtime, ships minimal JS, and pairs naturally with a single-file-component style that suits the app's modest UI surface.
- **Web search toggle**: A sidebar or composer button that enables/disables the SDK's built-in web search tool for the current session. Web search is available but currently excluded from `ALLOWED_TOOLS`; toggling it adds/removes the tool's qualified name from the list passed to `ClaudeAgentOptions`. The UI should clearly indicate when web access is active.
- **Markdown rendering in messages**: Message content is currently rendered as plain text via `textContent`, so Claude's Markdown output (headings, lists, code blocks, bold, links) shows as literal characters. Render it as Markdown instead — `marked` for parsing plus `DOMPurify` to sanitize the HTML before insertion. This makes URLs clickable for free via autolinking; just post-process anchors to add `target="_blank"` and `rel="noopener noreferrer"`.
- **Model version statusline**: Display the active Claude model version and effort level (e.g. `claude-sonnet-4-6 · effort: high`) in a small statusline element in the UI — footer or sidebar — so the user can always see which model and effort setting is backing the current session. Read both values from env vars already used by `agent.py` and expose them via a `/api/config` endpoint; the frontend fetches it once on load. Note: with a model router, values reflect configured alias and client-side effort — routers don't return effort and typically echo the alias, not the resolved version.
