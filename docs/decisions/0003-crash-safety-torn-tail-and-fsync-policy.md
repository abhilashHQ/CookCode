# 0003. Crash safety: torn tail, fsync policy and atomic fork

Status: accepted (M0)
Date: 2026-10-26

## Context
A process can die in the middle of writing a line, and the machine can lose power with data still in the page cache. The log must stay readable, and an action with side effects must never happen without its intent being on disk first.

## Options considered
- `fsync` after every event: safest, but adds disk latency to every streamed item.
- Never `fsync`: fastest, but a crash can lose the record of a command that already ran.
- `fsync` only where write-ahead matters: one sync per tool call.
- For fork: check that the target is absent, then rename. This races and can overwrite a session.

## Decision
`JsonlSessionLog.append` writes one line and flushes. It calls `os.fsync` only when `body.type` is in `DURABLE_TYPES` (`tool_call_started`) or the caller passes `durable=True`. A line without a trailing newline is a torn tail: `_read_events` stops at it, and `_truncate_torn_tail` removes it when a log is opened.

`JsonlSessionStore.fork` writes the prefix to a uniquely named tmp file (`new_id()` in the name), fsyncs it, then publishes it with `os.link(tmp, target)`. `link` fails atomically with `FileExistsError` if the target exists, so there is no race and no overwrite. It then fsyncs the directory and always unlinks the tmp name in `finally`.

## Consequences
- A tool's intent is durable before the tool runs. The fsync also persists every earlier event, because it syncs the whole file.
- Events after the last fsync can be lost on power failure. The design assumes the loss is a suffix of the file. The cost is about one fsync per tool call.
- Only the final line can be torn. A complete but corrupt line raises a validation error, so no corruption is skipped silently.
- Readers never see a partial fork, because the target appears only after a fully synced file is linked. A crash can leave an orphan `.tmp` file. Nothing sweeps them, but `list_sessions` ignores them.
- `os.link` needs a filesystem with hard links.
- Creating a session file in `open(create=True)` does not fsync the directory.

## What would change it
Losing newest non-durable events breaking resume would add types to `DURABLE_TYPES`. A filesystem without hard links would force a different publish step.
