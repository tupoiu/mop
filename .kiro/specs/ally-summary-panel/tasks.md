# Implementation Plan

> Boundary direction (from design.md): `events / config / db → ally → agent → main`. Frontend consumes the two new SSE event shapes only.

- [ ] 1. Foundation: configuration and event contracts

- [x] 1.1 (P) Add Ally configuration values to settings
  - Extend the application settings to read a late-window value and a side-model id from the environment, applying the documented defaults when unset.
  - Default the late window to `21:30-05:00` and the side model to the Haiku model id when the variables are absent or blank.
  - Keep settings as a pure environment reader: store the raw window string; do not parse or validate the window here.
  - Observable: loading settings with the variables unset yields the two defaults; setting them overrides the values, verified by a settings test.
  - _Requirements: 7.1_
  - _Boundary: Settings_

- [x] 1.2 (P) Add Ally SSE event types
  - Add a metrics event (agent words, user words, message count, UK time) and a summary event (topic, classification, the same metrics, UK time, warning flag) to the event union.
  - Confirm both events serialize through the existing generic serializer with no id line (neither carries a message ordinal).
  - Observable: a serializer test shows each event renders as its named SSE block with the expected JSON payload and no `id:` line.
  - _Requirements: 1.2, 5.1, 6.1, 8.1, 8.2_
  - _Boundary: events_

- [ ] 2. Core: Ally domain module

- [x] 2.1 (P) Implement deterministic Ally computations with unit tests
  - Compute conversation metrics from stored messages: total assistant words, total user words, and message count, counting only text messages and excluding tool-call, tool-result, and error rows.
  - Compute the current UK time (Europe/London) server-side, honouring BST/GMT, returned as a display string; accept an injectable "now" for deterministic tests.
  - Parse the configured late window string into a start/end time, falling back to the default window when the value is missing or malformed.
  - Evaluate window membership with midnight-crossing support, and derive the warning condition (true only when inside the window and the class is Programming-adjacent or Scientific).
  - Observable: unit tests cover word/message counting with tool rows excluded, a BST instant and a GMT instant, valid/invalid/empty window strings, midnight-crossing membership, and the warning matrix across all four classes inside and outside the window.
  - _Requirements: 4.1, 4.2, 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 6.4, 7.2, 7.3_
  - _Boundary: ally_

- [x] 2.2 Implement side-model analysis and event builders with unit tests
  - Add the conversation analysis routine that calls the side model once over a size-capped transcript and returns a short topic phrase and a classification constrained to the four allowed categories.
  - Parse the model response defensively: malformed/empty output or an unknown category collapses to a neutral placeholder topic and the `Other` class, and the routine never raises to its caller.
  - Add the two event builders that assemble the metrics event and the summary event (including the warning flag) from computed metrics, UK time, the late window, and the analysis result.
  - Observable: unit tests with a mocked model show valid JSON producing topic+class, and malformed/empty/unknown-class inputs producing the placeholder topic and `Other`; builder tests show correct payloads including a true and a false warning case.
  - _Requirements: 2.1, 2.3, 3.1, 3.3_
  - _Depends: 1.1, 1.2, 2.1_
  - _Boundary: ally_

- [ ] 3. Integration: emit Ally events across the turn lifecycle

- [x] 3.1 Wire Ally events into the streaming turn with concurrency
  - At turn start (user message already stored), emit the metrics event first so length and UK time reflect the just-sent message.
  - Launch the side-model analysis concurrently with the main turn so streamed assistant text is never delayed; await the analysis only at turn completion, bounded by a timeout, and cancel the analysis task on the error/early-exit paths.
  - On completion, recompute metrics over the updated history (now including the assistant reply), emit the summary event with topic, classification, refreshed metrics, UK time, and warning, then emit the terminal done event.
  - On analysis timeout or failure, still emit the summary event with the placeholder topic and `Other` class followed by done, leaving the chat turn unaffected.
  - Observable: integration tests with a mocked model and injected clock show the first event is the metrics event, text events precede the summary event, the summary event precedes done, the warning flag matches a late+Scientific scenario, and an analysis failure still yields summary-then-done with no orphaned task.
  - _Requirements: 2.2, 5.5, 8.1, 8.2, 8.3_
  - _Depends: 2.2_
  - _Boundary: agent stream_turn_

- [ ] 4. Frontend: Ally Panel UI

- [x] 4.1 (P) Add panel markup and styling
  - Add the Ally Panel to the sidebar with elements for topic, classification, UK time, the length string, and a warning sign.
  - Style the neutral/positive default state and a red warning state that reveals the warning sign.
  - Observable: loading the page shows the panel in the sidebar in its neutral empty state; applying the warning state renders red with the warning sign visible.
  - _Requirements: 1.1, 1.2, 1.3, 6.1_
  - _Depends: 1.2_
  - _Boundary: index.html, styles.css_

- [x] 4.2 Render Ally events and reset panel state
  - Handle the metrics event by updating the length string (formatted as `{A}/{U}W (A/U), {M}M`) and UK time; handle the summary event by updating topic, classification, time, length, and toggling the red warning state from the warning flag.
  - Reset the panel to its neutral empty state when switching sessions and when starting a new chat, and ensure a fresh page load starts empty (ephemeral, no persistence).
  - Observable: driving the SSE consumer with stubbed metrics/summary events updates the four fields and toggles the warning class; selecting another session or starting a new chat clears the panel back to the empty state.
  - _Requirements: 1.4, 1.5, 3.2, 5.1, 8.1, 8.2_
  - _Depends: 1.2, 4.1_
  - _Boundary: app.js_

- [ ] 5. Validation: end-to-end panel coverage and full suite

- [x] 5.1 Extend the E2E stub and add panel end-to-end tests
  - Extend the end-to-end stub's SSE fixture to include a metrics event and a summary event so the real frontend renders the panel during a stubbed turn.
  - Add end-to-end tests asserting the panel shows all four fields in the sidebar after a turn, renders the red warning state with the warning sign when the stubbed summary sets the warning flag, and returns to the neutral empty state on new chat / fresh load.
  - Run the full lint, type-check, unit, and e2e suites and confirm they pass.
  - Observable: the new e2e tests pass and the full suite (lint, types, unit, e2e) is green.
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 6.1_
  - _Depends: 3.1, 4.2_

## Implementation Notes
- Baseline `uv run poe lint` (full repo) is non-green at HEAD: 3 pre-existing errors (`tests/test_main_startup.py` F841 x2, `tests/test_streaming.py` F401), unrelated to this feature. Reviewers should scope lint to changed files (`uv run ruff check <files>`); do not treat these as task regressions.
- New `Settings` fields must carry dataclass defaults — existing code constructs `Settings(...)` directly (e.g. `tests/test_agent.py::_settings`) and would break otherwise.
- E2E live run is blocked in this environment: headless Chromium fails to launch (`libnspr4.so` missing; OS-dep install needs sudo). Affects ALL e2e tests, pre-existing. The panel e2e tests (`tests_e2e/test_ally_panel.py`) are written and COLLECT cleanly; they require a manual run (`uv run poe test-e2e`) in an environment with Playwright browser system deps installed (`playwright install-deps chromium`).
