# 0012. Budgets and the turn cap

Status: provisional, confirm at the M1 gate
Date: 2026-10-26

## Context
An agent that loops without limit burns money and time. The loop needs hard stops that work without a price table or a real model, and each stop must end the run through the normal close-out so the log stays valid.

## Options considered
- No limits: nothing to tune, nothing to stop a runaway.
- Limits checked mid-step: tighter, but can cut a call from its result.
- Limits checked at step boundaries, plus a wall-clock watchdog that uses the cancel path.
- Cost limit now: needs a price table that does not exist until M3.

## Decision
`Limits` has four fields and `AgentConfig` defaults to `Limits(max_turns=50)`.

- **Turns.** A turn is one model request, counted when the request starts, so a failed request counts. At a boundary, `turns >= max_turns` appends `budget_exceeded(turns)` and ends `budget_exceeded`.
- **Tokens.** Counted for this run as `usage.total_input + usage.output`, checked at the boundary. One request can overshoot.
- **Wall clock.** A watchdog task sleeps `max_wall_s` and cancels the internal token with reason `wall_clock`. The run ends `budget_exceeded` with a `wall_clock` event instead of `interrupted`. It is cooperative (0011).
- **Cost.** `Agent.__init__` raises `ValueError` if `max_cost_usd` is set.

Boundary order: steering, cancel, turns, tokens.

**Gate item for the owner:** the cap of 50 is provisional. It is a guess with no data behind it. Recommendation: keep 50 through M5 and set it from the turn counts of real runs at M6. When the cap hits, the last step's results are in the log but the model has not seen them.

## Consequences
- Budgets are per `run()`. A second `run()` on the same session starts at zero turns, tokens and elapsed time. A session-wide budget needs a client or a later change.
- Resumed sessions get fresh budgets.
- The wall-clock limit shares the weakness of 0011: a tool that ignores the token delays it.
- Reasoning tokens are not added separately.
- Limits are recorded in `session_started`, which is written once. A later `run()` with different limits leaves no record of the change.

## What would change it
Real-run turn distributions at M6, a price table (M3) for the cost limit, or a requirement for a session-wide budget.
