# 0003. Crash safety: torn tail, fsync policy and atomic fork

Status: accepted (M0)
Date: 2026-10-26

## Context
A process can die in the middle of writing a line, and the machine can lose power with data still in the page cache. The log must stay readable, and an action with side effects must never happen without its intent being on disk first.

## Options considered
- `fsync` after every event: safest, but adds disk latency to every streamed item.
- Never `fsync`: fastest, but a crash can lose the record of a command that already ran.
- `fsync` only where write-ahead matters: one sync per tool call.
- A buffered writer: simple, but a failed flush keeps the unwritten remainder in the buffer and writes it on the next append, which then reuses the same `seq`.
- For fork: check that the target is absent, then rename. This races and can overwrite a session.

## Decision
`JsonlSessionLog.append` writes one line through a raw `O_APPEND` descriptor with a loop over `os.write`, and remembers the offset where the last good line ended. It calls `os.fsync` only when `body.type` is in `DURABLE_TYPES` (`tool_call_started`) or the caller passes `durable=True`. A line without a trailing newline is a torn tail: `_read_events` stops at it, and `_truncate_torn_tail` removes it when a log is opened. If the write or the fsync fails, `append` truncates the file back to the last good offset and re-raises, so the failed event leaves no bytes and does not consume a `seq`. If that truncate also fails, the log is poisoned and refuses appends until it is reopened, where torn-tail repair and the seq scan restore a consistent state.

`JsonlSessionStore.fork` copies the raw bytes of each line up to `at_seq` (parsing only to find the seq) to a uniquely named tmp file (`new_id()` in the name), fsyncs it, then publishes it with `os.link(tmp, target)`. `link` fails atomically with `FileExistsError` if the target exists, so there is no race and no overwrite. It then fsyncs the directory and always unlinks the tmp name in `finally`.

## Consequences
- A tool's intent is durable before the tool runs. The fsync also persists every earlier event, because it syncs the whole file.
- Events after the last fsync can be lost on power failure. The design assumes the loss is a suffix of the file. The cost is about one fsync per tool call.
- Only the final line can be torn. A complete but unparseable line raises `SessionCorruptError` with the file and line number, on open and on read, so no corruption is skipped silently. The lock is released when open fails.
- Readers never see a partial fork, because the target appears only after a fully synced file is linked. A crash can leave an orphan `.tmp` file. Nothing sweeps them, but `list_sessions` ignores them.
- `os.link` needs a filesystem with hard links.
- Creating a session file in `open(create=True)` fsyncs the directory, so the file is reachable after power loss.
- Fork keeps fields written by a newer schema, because it never re-serializes.

## What would change it
Losing newest non-durable events breaking resume would add types to `DURABLE_TYPES`. A filesystem without hard links would force a different publish step.
