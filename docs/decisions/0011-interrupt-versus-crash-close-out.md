# 0011. Interrupt versus crash close-out

Status: accepted (M1), with a gate concern below
Date: 2026-10-26

## Context
A run can stop on request (user interrupt, wall-clock limit) or without warning (SIGKILL, power loss, a bug, `Task.cancel()`). Either way the next request needs every tool call to have a result.

## Options considered
- Always write nothing and repair on resume: one path, but every Ctrl-C leaves a log that needs repair and does not say why it stopped.
- Always write a close-out: impossible after a crash.
- Two paths with one status rule: the loop closes out what it can, and repair handles the rest.

## Decision
`run()` makes an internal `CancelToken`. The caller's token and a wall-clock watchdog both cancel it, with the caller's reason (default `"user"`) or `"wall_clock"`. Providers and tools see only the internal token.

**Graceful path.** The loop closes out every call of the current step itself, then appends `interrupted(phase, killed)`, or `budget_exceeded(wall_clock)`, then `session_finished`. The log needs no repair.

**Crash path.** Nothing is written. `Task.cancel()` propagates `CancelledError` and the handler's `finally` still kills the process group (0006). The next `run()` calls `plan_repairs` before anything else and appends a `repaired` event plus a `ToolResult` per dangling call.

**One status rule for both.** A call that never started is `interrupted` ("not executed"). A started read-only call that did not complete is `interrupted`. A started mutating or external call that did not complete is `unknown`. Repair reads the class from `tool_call_started`, never from the current registry. Repair never re-executes a call.

**Gate concern.** Cancellation is cooperative only. A tool that ignores the token stalls a graceful interrupt and the wall-clock budget until it returns. There is no grace-period hard kill. Options:
- Grace timeout, then hard task cancellation, which has crash semantics and is repaired next run.
- Accept it, because every real tool goes through `env.exec`, which honors the token.

Recommendation: accept for M1, and add the grace timeout when the first tool or provider that bypasses `env.exec` or ignores the token appears (M2 or M3).

## Consequences
- After an interrupt, the log says why the run stopped.
- Two code paths apply the same rule. Tests cover both.
- `Interrupted.killed` lists calls that were running when the cancel landed, including one that ignored it and returned `ok`.
- Only an `exit_status` of `killed` maps to `unknown` on the graceful path. A timeout keeps the status the tool returned, normally `error`, even for a mutating tool.

## What would change it
A tool or provider that cannot honor the token, or a requirement to bound interrupt latency.
