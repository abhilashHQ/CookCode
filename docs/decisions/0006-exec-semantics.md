# 0006. Command execution semantics

Status: accepted (M0)
Date: 2026-10-26

## Context
The agent's main tool is running shell commands. Commands hang, spawn background children, print megabytes, and get cancelled. The model also needs to see the part of the output that matters.

## Options considered
- Persistent shell session: shell state carries over, but state lives outside the log and is lost on resume.
- Fresh process per call: stateless, simple to reason about and to kill.
- Reading stdout through `asyncio.subprocess.PIPE`: the obvious choice, but on Python 3.12 `proc.wait()` does not resolve until every holder of that pipe closes it. A backgrounded child (`sleep 30 &`) holds it, so `wait()` blocks until the timeout.

## Decision
`LocalEnvironment.exec` (`src/harness/env/local.py`) runs `bash -c cmd` with `start_new_session=True`, stdin on `/dev/null`, and stderr merged into stdout. It creates its own `os.pipe()`, hands the write end to the child, closes it in the parent, and reads the read end with `connect_read_pipe`. The subprocess object then owns no pipe, so `proc.wait()` returns when bash exits.

Cleanup runs in `finally`: kill the process group with `SIGKILL`, cancel the cancel-waiter, await `wait()`, drain the pump for at most five seconds, close the transport. This runs after normal exit too, so background children never outlive the call. It also runs when the awaiting task is cancelled, for example by a `TaskGroup` tearing down, so cancellation cannot orphan a process group.

Output goes through `_CappedBuffer`, which keeps the first `max_output_bytes // 2` bytes and the remaining budget from the end, and inserts a marker with the number of dropped bytes. When nothing was dropped it decodes both parts in one pass, so a character on the split is never broken. When bytes were dropped it trims a partial character from the end of the head and skips continuation bytes at the start of the tail, and counts them as dropped.

Spawning runs as a shielded task. If the awaiting task is cancelled while `create_subprocess_exec` is in flight, `exec` waits for the pid, kills the group and reaps it before re-raising. Everything after the spawn sits inside the `finally` block.

## Consequences
- Memory per call is bounded by `max_output_bytes`.
- Head plus tail keeps the command's start (what it did) and the end (where it failed).
- `cd` and `export` do not persist between calls. The working directory becomes logged agent state in M1/M2.
- A process that leaves the group with `setsid` escapes the kill. The pump gives up after five seconds, so such a process cannot hang the call.
- `exit_code` is `None` on timeout and cancel.
- Linux only: `os.killpg` and process groups.

## What would change it
A task class that needs real interactive shell state, or a sandbox backend (container, remote) where the group kill is replaced by the backend's own kill.
