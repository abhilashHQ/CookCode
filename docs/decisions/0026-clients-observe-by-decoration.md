# 0026. Clients observe the core by decorating its interfaces

Status: accepted (M3)
Date: 2026-10-26

## Context
The plain CLI has to show streaming text, tool calls, results, retries and the final status. The core never prints (spec section 3), the loop logs only finished items, and display deltas are not in the log. The CLI needs both streams without the core growing a callback API that every future client (headless, Harbor, TUI, Flagship 2) would have to share.

## Options considered
- Add an event bus or callbacks to `Agent`: one more core API, and a slow or failing subscriber runs inside the loop.
- Tail the session file: sees log events, but not deltas, and polls.
- Wrap the two interfaces the core already depends on: `DisplayProvider` passes stream events through to a sink, and `ObservedLog` calls a sink after each successful `append`.

## Decision
Decoration, in `cli/observe.py` (moved out of `cli/terminal.py` in M4 so the headless client needs no terminal code). Both wrappers satisfy the existing protocols, so `Agent` cannot tell them from the real thing. Each sink sits behind `_Observer`, which calls it, and on its first exception disables it and keeps the error. The append or the stream event still goes through. A broken pipe on stdout or a rendering bug can therefore never make `append` raise after the event is already on disk, which the loop would read as a failed write.

## Consequences
- Zero core changes for M3's client. The headless JSONL client (M4) is an `ObservedLog` whose sink writes JSON lines.
- Observers see events in commit order, after the fsync policy has applied, so a client never shows something the log does not have.
- A sink runs synchronously inside `append`, so a slow one slows the loop. Sinks must be cheap; anything slow should queue.
- `DisplayProvider` closes the inner stream in `finally`, so wrapping does not break the loop's `aclose` on interrupt.

## What would change it
A client that needs events the log does not carry (per-chunk timing, for example), or several clients on one live session, which would justify a fan-out with backpressure.
