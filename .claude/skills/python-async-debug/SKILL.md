---
name: python-async-debug
description: Debug Python asyncio hangs, deadlocks, and "the test ran but nothing happened" failures. Use when an async test or service hangs, when output is missing, or when an await chain behaves out of order.
allowed-tools: Read, Bash, Grep, Edit, Write
argument-hint: <symptom>
---

# python-async-debug

A method, not a checklist of footguns. When async code is misbehaving, follow these steps in order. Stop as soon as the cause is obvious.

## 1. Make the hang visible

Async bugs love to hide as silence. Before you guess, force the failure to surface.

- **Per-test timeout** (highest leverage): add `pytest-timeout` and set `timeout = 5` (or shorter) in `[tool.pytest.ini_options]`. Hangs convert into tracebacks pointing at the exact awaited line.
- **Outer command timeout**: run with a tight harness/CLI timeout (10–15s for fast suites). Don't pad — long timeouts hide the problem.
- **Single test, not suite**: shrink to one test or one code path so the traceback points somewhere specific.

If the symptom isn't a hang but "did nothing", check stdout/stderr separately and look for swallowed exceptions on cancelled tasks (often only visible as `Task exception was never retrieved` warnings on shutdown).

## 2. Locate the stuck task

You need to know *what* is awaiting *what* at the moment of the hang. Pick the lightest tool that gives you that.

- `PYTHONASYNCIODEBUG=1` — logs slow callbacks, never-awaited coroutines, and resource warnings. Run the failing command with this prefix first; it's free.
- `faulthandler.dump_traceback_later(N)` at the start of the suspect code — dumps every thread's stack after N seconds. Works even when the loop itself is wedged.
- `asyncio.all_tasks()` from inside a debugger or a logged hook — lists every pending task. The one you care about is usually obvious from its `get_coro()` repr.

Stop when you can name the awaited call that never completes.

## 3. Check the loop assumption at that call

Once you have the stuck call, ask one question: **does this actually run on the event loop, or does it bottom out in a thread?**

- Many "async" libraries are sync libraries with an async-shaped façade — they dispatch blocking calls to a worker thread. The bug surface is at that boundary. Read the library's `connect`/`open` source: if it's a `Thread`, an executor pool, or a `to_thread` call, treat its lifecycle (start, close, reuse) as the prime suspect.
- If it's true async I/O, the suspect is usually a missing `await`, an unawaited task, or a cancelled-then-awaited coroutine.

This is the step where most async bugs resolve. The lifecycle of a thread-backed object behaves nothing like the lifecycle of a pure coroutine, and treating them as interchangeable is the recurring failure mode.

## 4. Bisect

If steps 1–3 didn't reveal it: shrink the failing path until it does. Inline the helper, drop the fixture, run the smallest async snippet that reproduces. The surface area of an async bug usually collapses to ~5 lines once you isolate it.

## 5. Verify the fix the same way you found the bug

- Re-run with the same per-test timeout. A "fixed" hang that only passes without a timeout is not fixed.
- Re-run the full suite to catch regressions (other tests sharing the same fixture or library).
- If the bug was at a thread/loop boundary, write the test that would have caught it earlier — it's almost always a 5-line round-trip test.

## What this skill is not

- Not a list of asyncio footguns to memorize. The footguns change; the method doesn't.
- Not a fix generator. Use it to *locate* the bug. Apply the fix yourself once you understand it.
- Not for non-hang bugs that aren't async-shaped (use `kiro-debug` or normal debugging instead).
