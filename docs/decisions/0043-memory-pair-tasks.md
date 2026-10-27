# 0043. Measuring memory with paired suite tasks

Status: accepted (M8 gate, 2026-10-27)
Date: 2026-10-27

## Context
Memory only shows its value in a second session that needs what the first one learned. The 24 suite tasks are independent, so memory can only add tokens there. Measuring it needs tasks built in sequence, where the second task is cheaper or more likely to pass if the agent remembers something specific from the first.

## Options considered
- Run existing tasks twice in a row: the second run would just be a repeat. That tests recall of an answer, not of knowledge about the repository.
- Multi-session benchmarks from the literature: they target chat assistants, not coding repositories, and they don't run in our suite.
- Paired tasks in the local suite: task A works in a repository with a non-obvious convention or setup step. Task B is a different change in the same repository that fails without that knowledge.

## Decision
- 6 pairs, 12 tasks, kind `memory`: codegen (generated code must be regenerated, not edited), testenv (tests only run through `run_tests.sh`, which sets `APP_ENV=test`), i18n (strings go through the locale catalog), migrations (schema changes need a migration file), registry (commands register in a central table), money (amounts are integer cents, percentages round half up).
- `after = "<A's id>"` in B's `task.toml`. B starts from a fresh copy of A's fixture, not A's end state, so B measures memory and not leftover edits. `suite.chains()` schedules A then B in the same attempt, with that attempt's memory directory.
- B's checks require the convention to be followed and the convention's own tests to still exist (`keeps`), so deleting a failing test does not pass.
- The metric is B's pass rate and B's tokens and turns under none, notes and facts, 2 attempts each. A's numbers are reported but A cannot benefit.
- `validate.py` expects 12 memory tasks, and every task passes its oracle.

## Consequences
- 6 pairs × 2 attempts × 3 arms is 36 B runs, enough to see a large effect and too few for a small one. 0037's method reports the interval, not a verdict.
- Each convention could also be discovered by reading the repository. The question is whether memory makes discovery cheaper, which is the realistic case.
- The pairs live in the same suite, so replay covers them once their cassettes are recorded under the base arm.

## What would change it
B tasks turning out solvable without the convention (then the pair measures nothing), or results with intervals too wide to read, which would mean more pairs.
