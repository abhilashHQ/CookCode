# 0008. Synchronous store I/O

Status: accepted (M0)
Date: 2026-10-26

## Context
The harness is an async program, but `JsonlSessionLog` uses blocking file writes, `flock` and `fsync`. Blocking calls inside the event loop stall every other task while they run.

## Options considered
- Wrap each store call in `asyncio.to_thread`: never blocks the loop, but adds thread hops, ordering questions and an async `SessionLog` protocol.
- Dedicated writer task with a queue: decouples callers from disk, but acknowledgment before durability weakens write-ahead.
- Plain blocking calls: simplest, with a bounded cost.

## Decision
`SessionLog` and `SessionStore` (`src/harness/store/base.py`) are synchronous protocols, and `JsonlSessionLog.append` writes and flushes inline.

## Consequences
- Appends are small single lines written to the page cache. The only slow call is `fsync`, and it runs for `tool_call_started` or an explicit `durable=True`.
- The intended loop appends the intent before running a tool and must wait for that write anyway, so the write-ahead ordering holds without extra machinery. M0 has no loop yet, so this is the design, not something a test exercises.
- Cost that remains: opening a session reads the whole file, and `read` re-reads it each call. Both block.
- Concurrent tool execution later will hit the same blocking appends. Each append is short, but a slow disk would show up as loop latency.
- Keeping the protocol synchronous keeps the store trivial to test and keeps `core` free of async concerns.

## What would change it
Traces showing append or open latency stalling streaming or concurrent tools. The response is to call the store through `asyncio.to_thread` at the loop boundary first, and change the protocol only if that is not enough.
