# Research & Design Decisions

## Summary
- **Feature**: `ally-summary-panel`
- **Discovery Scope**: Extension (adds a panel + a concurrent side-model analysis to the existing single-turn streaming pipeline)
- **Key Findings**:
  - The turn lifecycle is fully expressed as a stream of typed SSE events from `app/agent.py::stream_turn`; new panel data can ride the same stream as two new event kinds without touching `app/main.py`.
  - `serialize()` in `app/events.py` is generic over the event union (it only special-cases an `id:` line for events with `message_ord`), so new dataclass events serialize automatically once added to `SSEEvent`.
  - The SDK `query()` is already the project's one model entry point and is mockable in tests via `monkeypatch.setattr("app.agent.query", ...)`. The same primitive serves the small side model — no new dependency (no direct Anthropic SDK, no HTTP client) is required.

## Research Log

### Integration point for panel updates
- **Context**: Requirements need updates on both user send (length) and assistant turn completion (full refresh), without blocking the streamed reply.
- **Sources Consulted**: `app/main.py` (`_send_message`, `_generate`), `app/agent.py` (`stream_turn`), `app/events.py` (`serialize`).
- **Findings**: `_send_message` appends the user message to the DB *before* invoking `stream_turn`, then serializes every yielded event verbatim. So `stream_turn` can yield an `ally_metrics` event as its first item (reflecting the just-stored user message) and an `ally_summary` event right before `DoneEvent`. `main.py` needs no change.
- **Implications**: All orchestration lives in `stream_turn`; the panel is a pure additive concern on the stream.

### Concurrency model for the side model
- **Context**: R2.2 / R8.3 require the topic+classification to be generated concurrently and never delay the streamed assistant text.
- **Findings**: Launching `asyncio.create_task(ally.analyze(...))` at turn start lets Haiku analysis run alongside the main `query()` iteration. The main loop continues yielding text deltas unaffected; the analysis result is awaited (with a timeout guard) only at `ResultMessage` time, just before emitting `ally_summary`.
- **Implications**: The analysis sees the conversation **through the latest user message** but not the in-progress assistant reply (which has not been persisted when the task starts). This is an intentional one-turn-lag trade-off documented below.

### UK time and late-window evaluation
- **Context**: R4 (Europe/London, BST/GMT) and R7 (single configurable window, midnight-crossing).
- **Findings**: Python 3.11 ships `zoneinfo` in the stdlib; `ZoneInfo("Europe/London")` handles BST/GMT automatically. No `tzdata` dependency needed on Linux. A window like `21:30-05:00` crosses midnight, so membership is `start <= t or t < end` when `start > end`.
- **Implications**: All time logic is pure and stdlib-only; functions accept an optional injected `now` for deterministic tests.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| New `app/ally.py` service + new SSE events (chosen) | Domain module computes metrics/time/window/analysis; two new events ride the existing stream | Small blast radius, reuses stream + `query`, testable pure functions, `main.py` untouched | `stream_turn` grows slightly | Matches existing layering (events/config/db → service → runtime) |
| Separate `/api/ally` polling endpoint | Frontend polls a REST endpoint after each turn | Decouples from stream | Extra round-trips, second auth path, duplicated lifecycle logic, racey vs. stream | Rejected: more moving parts for no benefit |
| Client-side metrics + class | Compute counts/time/class in JS | No backend change for metrics | Can't run the model client-side; UK time would use client clock (violates R4 server-side) | Rejected |

## Design Decisions

### Decision: Single side-model call returns both topic and classification
- **Context**: R2 (topic) and R3 (classification) are two facets of the same "analyze this conversation with a small model" problem.
- **Alternatives Considered**:
  1. Two separate `query()` calls (one for topic, one for class) — double latency/cost.
  2. One call returning a small JSON object `{topic, classification}`.
- **Selected Approach**: One Haiku call instructed to return strict JSON `{"topic": ..., "classification": ...}`; parsed defensively.
- **Rationale**: Generalization lens — one interface satisfies both requirements; halves model calls.
- **Trade-offs**: Requires robust JSON parsing; mitigated by fallback (placeholder topic + `Other`).
- **Follow-up**: Validate `classification` against the allowed set; coerce unknowns to `Other`.

### Decision: Reuse SDK `query()` for the side model (build-vs-adopt)
- **Selected Approach**: Call `claude_agent_sdk.query()` with `ClaudeAgentOptions(model=<haiku>)`, no tools, no `resume`.
- **Rationale**: Already a project dependency and the single tested model path; avoids introducing the raw Anthropic SDK/HTTP client.
- **Trade-offs**: Slightly heavier than a bare messages call, but consistency and testability win.
- **Follow-up**: Model id defaults to `claude-haiku-4-5-20251001` (Haiku 4.5), overridable via `ALLY_SUMMARY_MODEL`.

### Decision: Config reads raw strings; `ally.py` owns domain parsing
- **Context**: `app/config.py` is a pure env reader; late-window parsing with fallback is domain logic.
- **Selected Approach**: `Settings` gains `ally_late_window: str` (default `"21:30-05:00"`) and `ally_summary_model: str`. `ally.parse_late_window()` parses with a default-on-invalid fallback (R7.2).
- **Rationale**: Keeps dependency direction `config → ally` (never upward) and config free of domain logic.

### Decision: Message-count `M` counts text-kind user+assistant messages
- **Context**: R5.3 ("number of user and assistant messages") vs. R5.4 (exclude tool entries from word counts).
- **Selected Approach**: For internal consistency, `M` counts only `kind == "text"` user/assistant messages; tool_call/tool_result/error rows are excluded from both word counts and `M`.
- **Rationale**: Matches the intuitive "messages exchanged" reading and avoids inflating `M` with tool plumbing. (Confirmed acceptable with the user during requirements.)

## Risks & Mitigations
- **Side-model latency delays the `done` event** — bound `ally.analyze` with `asyncio.wait_for` (timeout, e.g. ~8s); on timeout cancel the task and emit `ally_summary` with placeholder topic + `Other` class so the panel and `done` are never withheld.
- **Malformed JSON from Haiku** — defensive parse; fall back to placeholder topic + `Other` (R2.3, R3.3).
- **Large conversations inflate side-model prompt/cost** — cap the transcript handed to the side model (e.g. last N text messages / truncated) ; metrics still computed over the full session.
- **Topic excludes the latest assistant reply** (concurrency trade-off) — accepted; topic tracks user direction, which is the dominant signal. Documented as a Boundary/Non-Goal nuance.
- **Displayed UK time is a snapshot, not a live-ticking clock** — acceptable per R4 (current time at update); panel refreshes each turn.

## References
- `app/agent.py`, `app/events.py`, `app/main.py`, `app/config.py`, `app/db.py` — existing streaming/turn/persistence patterns this design extends.
- Python `zoneinfo` (stdlib, 3.9+) — Europe/London BST/GMT handling.
- Haiku 4.5 model id `claude-haiku-4-5-20251001` — default side model.
