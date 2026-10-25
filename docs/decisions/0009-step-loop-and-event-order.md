# 0009. Step loop and event order

Status: accepted (M1)
Date: 2026-10-26

## Context
The loop turns the log into model requests, runs tools and writes the outcome back. A crash at any point must leave a log that repair can fix. Outside input (steering, budgets, interrupts) also needs fixed places to take effect, without splitting a tool call from its result.

## Options considered
- Turn-based loop with a nested tool loop: easy to read, but budgets and steering have no natural place to apply.
- Check anything anywhere, including between tool calls: responsive, but a call can be left without its result, and the spec requires the next request to be valid for every provider.
- The step as the unit, with checks at its boundaries: one fixed place for outside input. A running step can only be shortened by cancellation.

## Decision
`Agent` (`src/harness/agent/loop.py`) runs steps. At each boundary it drains steering, checks the cancel token, then checks the turn and token budgets. A step then:

1. builds the request from the log (`build_request`),
2. appends `model_request_started`,
3. streams, appending each finished item as an `item` event when it arrives,
4. appends `model_request_finished`,
5. runs the response's tool calls.

With no tool calls the run ends `done`, unless steering is queued, which forces another step.

Per call, the order is `tool_call_started` (fsynced), handler, `tool_call_finished`, result `item`. The last two are separate appends. Unknown tool names and invalid arguments get an error result straight away, with no started or finished event.

`session_started` is written once, on an empty log. Each `run()` first appends repair events (0011), then the optional prompt, and ends with its own `session_finished`. A `run()` reads nothing from the previous run except the log.

## Consequences
- A call and its result are never split by steering or budgets.
- The turn check is exact. The token check runs before a step, so one step can overshoot the token budget.
- Between `tool_call_finished` and the result `item` there is a window where a crash loses the outcome of a side effect that already happened. Repair covers it, and the chaos experiment measures it.
- Every model request counts as a turn, including one that fails.
- Calling `run(None)` on a finished session makes one more request.

## What would change it
A need for input mid-step, such as cancelling one tool without ending the run. A store that appends several events atomically would let finish and result land together and close the window.
