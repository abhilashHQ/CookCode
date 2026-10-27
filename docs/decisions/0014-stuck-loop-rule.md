# 0014. Stuck-loop rule

Status: accepted (M1 gate, 2026-10-26)
Date: 2026-10-26

## Context
A model can repeat the same call and get the same result, turn after turn. The spec (section 5) defers "done detection beyond `end_turn`" to the M1 gate and says to measure it at M6. M1 uses a fake provider and a deterministic scenario, so nothing in M1 can loop in a way that tests a rule. The loop currently has no stuck-loop code. The turn cap (0012) is the only backstop.

## Options considered
- **No rule, turn cap as the backstop.** Cost: a stuck run burns up to 50 requests before it stops. No false positives, no new code.
- **Stop after N identical consecutive calls.** Same tool and same arguments, N times in a row. Cost: false positives. Re-running the test suite after an edit is an identical call with a different outcome, and a hard stop kills a run that was making progress. Needs a definition of identical that includes the result.
- **Inject a nudge after N identical calls.** The loop appends a message telling the model it is repeating itself and should change approach. Cost: tokens and an extra event type or a reuse of the steer path. It cannot kill a healthy run, and the turn cap still ends one that ignores the nudge.

## Decision
**Gate:** Accepted at the M1 gate on 2026-10-26: no stuck-loop rule in M1. Revisit at M6 with measured repeated-call data, starting with a nudge message.

**Recommendation.** Ship M1 with no rule and the turn cap as the backstop. Nothing available now can measure a rule, and a wrong rule is harder to remove than a missing one. At M6, when real runs exist, test the nudge against no rule on the benchmark tasks. Define identical as same tool, same arguments and same result content, with N around 3, and log the nudge as an event so runs can be compared. If a rule is wanted before M6, pick the nudge. Stopping outright is the worst option because it trades a cost problem for silent failures.

## Consequences
- With no rule, a stuck run costs up to the cap in requests. At the provisional 50, that is a bounded and visible loss.
- Whatever is chosen touches `_execute` or `_step` in `loop.py` and possibly `events.py`. It does not touch the log format for existing events.

## What would change it
Measured stuck runs at M6, or an early real-model run at M3 that loops until the cap.

## Measured at M6 and M7 (2026-10-27)
`cookcode stats` counts repeat streaks: 3 or more consecutive identical call-and-result pairs, the definition above.
- Local suite, 24 sessions, 334 tool calls: 0 streaks.
- Terminal-Bench slice, 50 CookCode sessions, 1,221 tool calls: 0 streaks. 35 sessions ended `done`, 2 `error` (429 exhaustion) and 13 never logged `session_finished`, so they were cut off from outside the loop rather than stopped by the turn cap. None of them repeated itself.

The rule stays unbuilt. With zero streaks there are no runs to test a nudge on, and a rule with nothing to fire on can only add false positives. The measurement runs in every `stats` report, so a model that loops shows up as a nonzero count.
