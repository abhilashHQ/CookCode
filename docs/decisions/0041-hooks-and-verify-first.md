# 0041. Loop hooks, and verify-first as a hard lock

Status: accepted (M8 gate, 2026-10-27)
Date: 2026-10-27

## Context
Spec section 12 asks for verify-first: on a bug, reproduce it before fixing it, and prove the fix against the reproduction. A prompt can ask for that, but models skip the step when they think they see the fix. Enforcing it needs a place in the loop where the harness can refuse a tool call or refuse to end the turn. The same mechanism later serves sub-agent report contracts (0044) and user hooks (0046), so it had to be general without growing the loop.

## Options considered
For the mechanism:
- Special cases in the loop for each feature: fast to write, and the loop grows with every feature.
- Middleware wrapping every tool: sees calls but not the decision to stop.
- Hooks at two points, before a tool call and before the turn ends, plus one at the end of a run, each returning None or a block message.

For verify-first:
- Prompt only: free, unenforced.
- Soft nudge: warn on the first edit without a repro, then allow it.
- Hard lock: until a reproduction is registered, file tools may only create new files. The stop hook reruns the repro and blocks while it fails.

## Decision
- `agent/hooks.py`: `Hook(name, before_tool, before_stop, on_finish)` and `HookContext` (env, cancel, cwd, limits, history, `ask`).
  - A blocked tool call becomes its error result, before approval gating, so the user is never asked about a call that would be blocked anyway.
  - A blocked stop becomes a `[hook] message` note to the model, which keeps working. Each block is logged as `hook_blocked`, and the builder renders it from the log, so replay stays exact.
  - `AgentConfig.max_stop_blocks` (3) caps stop blocks per turn, so a hook that never passes cannot loop forever.
- `on_finish` runs once when a run ends done or on budget, before `session_finished`. Its side requests go through `ask(view, ...)`, which logs them under that view. The builder, replay and the counterfactual skip side views.
- Verify-first is a hard lock (owner's call):
  - The `repro` tool registers a command that must fail now. The registration is the latest ok `repro` result in the log, with no state outside it.
  - Before registration, `edit` and `write` may only create new files.
  - At stop, the hook reruns the registered repro and blocks while it fails. If nothing is registered but files changed, it blocks too.
- `cookcode eval repro <run>` judges offline whether a repro failed for the right reason. It must fail on the fixture plus the files the agent created, and pass on the reference solution plus those files.

## Consequences
- Bash can still change files before a repro exists. The lock covers the file tools, which is where models make their edits. Closing bash would mean parsing shell, and that cannot be done reliably.
- A wrong repro (one that fails for an unrelated reason) satisfies the lock. `eval repro` catches it after the fact. That is why the judge exists.
- The ablation is `vf-base` against `vf-on` on the 6 bugfix tasks, 2 attempts each, plus `eval repro` on the verify-first runs (0036).

## What would change it
Live runs showing the lock costing more turns than it saves in wrong fixes, or models routing edits through bash to get around it.
