# 0007. Cooperative cancellation with CancelToken

Status: accepted (M0)
Date: 2026-10-26

## Context
A user interrupt must stop a streaming model call or a running command, leave a consistent log, and keep partial results. `asyncio.Task.cancel()` raises `CancelledError` at an arbitrary await, which gives no controlled way to return partial output or report why it stopped.

## Options considered
- Rely only on task cancellation: no extra API, but the caller gets an exception instead of a result, and partial output is lost.
- Pass an explicit token and check it at defined points, keeping task cancellation as a safety net.

## Decision
One `CancelToken` (`src/harness/core/cancel.py`) wraps an `asyncio.Event`. `cancel(reason)` sets it, the first reason wins, and `cancelled`, `reason` and `wait()` expose it. It is passed to `Provider.stream(request, cancel)` and `Environment.exec(..., cancel=...)`.

- Providers check it between stream events. `FakeProvider` checks before each item, between text deltas and before each `ItemDone`, then yields `StreamEnd(stop_reason="aborted")`. Items already yielded stay valid. The item in flight is dropped.
- `exec` returns a cancelled result without spawning if the token is already set. Otherwise it waits on `proc.wait()` against `cancel.wait()` (with the timeout), kills the process group, and returns `ExecResult(cancelled=True, exit_code=None)` with the output so far.
- The `finally` cleanup in `exec` also handles raw task cancellation, so the token is the normal path and `Task.cancel()` is the fallback.

## Consequences
- Cancellation yields a normal result the loop can log as an interruption, with partial output.
- Every provider and environment implementation must honor the token. The contract is only a convention plus tests (`tests/test_fake_provider.py`, `tests/test_local_env_exec.py`).
- `CancelToken` is not thread-safe. `cancel()` must be called on the event loop's thread.
- A provider that never checks the token is stopped only by task cancellation.

## What would change it
A real provider adapter that cannot check the token between events because its SDK blocks, or a need to cancel from another thread or process.
