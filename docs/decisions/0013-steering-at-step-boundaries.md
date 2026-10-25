# 0013. Steering at step boundaries

Status: accepted (M1)
Date: 2026-10-26

## Context
A user types while the agent is working. The text should reach the model soon, without breaking a call/result pair and without losing the model's place in the log.

## Options considered
- Abort the stream and restart with the new text: fast, but wastes tokens and leaves a half-built step.
- Inject between tool results: faster, but it splits the call block from its results, which the spec forbids.
- Queue and apply at the next step boundary.
- Wait until the run ends: safe, and it amounts to a follow-up prompt.

## Decision
`Agent.steer(text)` appends to an in-memory deque. At each step boundary, `_drain_steering` appends one `SteerMessage` event per queued text, before the cancel and budget checks. `build_request` projects `SteerMessage` as a user message, so the model reads it like a prompt.

If a response has no tool calls but steering is queued, the run does not finish. It takes another step, so a message that arrives during the final answer is not dropped.

Text that arrives after the last boundary of a run stays queued. The next `run()` logs it at its first boundary, after that run's `user_message`.

## Consequences
- A call and its result are never separated by steering.
- Latency is up to one step: a long model stream or a long tool delays the message.
- The queue is memory only. A message not yet drained is lost in a crash, and the client must resend it. A drained message is in the log and survives.
- Draining happens even when the run is ending, so steering that arrived before a cancel or budget stop is logged.
- `steer` is meant for the loop's thread. It has no lock.
- The queue is one list for the agent. Two clients steering one agent share it.

## What would change it
A requirement for sub-step latency, such as cancelling a stream when the user types, or durable steering that survives a crash. The second would write the steer to the log on arrival, which needs a rule for where it lands relative to open calls.
