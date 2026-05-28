---
name: frontend-debug
description: Debug frontend behaviour by writing a headless Playwright script, running it, and reading the screenshots back into context. Use when the user reports a UI bug, wants to verify a frontend change works, or asks you to "try it" / "check it" against a running server.
allowed-tools: Read, Write, Edit, Bash
argument-hint: <what to verify or debug>
---

# frontend-debug

Diagnose frontend issues by running a headless Playwright script and reading the resulting screenshots back into the conversation.

## Flow

### 1. Write the script

Write a self-contained `scripts/debug_<slug>.py` that:
- Accepts `--url` and `--token` (or whatever auth the app uses) as CLI args
- Launches Chromium **headless**, no `slow_mo`
- Takes a screenshot **after each meaningful step** (page load, form submit, button click, assertion)
- Saves screenshots to `screenshots/<slug>/NN_description.png` (zero-padded, snake_case)
- Prints a one-line status per step so progress is visible in the terminal
- Asserts the expected outcome at the end and prints a clear ✓ / ✗ summary

### 2. Run it

```bash
uv run python scripts/debug_<slug>.py --token <token> [--url http://127.0.0.1:8000]
```

Check stdout for failures. If it errors, read the traceback and fix the script before proceeding.

### 3. Read the screenshots back

Use the `Read` tool on each PNG in order. The file path format is the absolute path, e.g. `/workspace/screenshots/<slug>/01_initial_load.png`.

When reporting findings to the user, reference screenshots inline like this:

> After clicking Send, the user bubble appears immediately (`screenshots/<slug>/04_enter_pressed.png`). The assistant response streams in within ~2s (`screenshots/<slug>/05_assistant_streaming.png`).

Don't just list file paths — describe what each screenshot shows and what it confirms or contradicts.

### 4. Report

State clearly: what worked, what didn't, and what the screenshots show. If a bug is visible in a screenshot, say which one and describe what you see.

## Notes

- If the dev server isn't running, start it first: `uv run poe dev &> /tmp/dev.log &` then `sleep 3` to let it start.
- Prefer reading a few key screenshots (initial state, after action, final state) over reading all of them.
- If an assertion fails mid-script, the later screenshots won't exist — read what was saved before the crash.
