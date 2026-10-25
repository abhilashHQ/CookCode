# Chaos experiment, M1

- Harness base commit: `2f6004ff4c8faca476ee6b9dacb81a406680e8fd`
- Date: 2026-10-25
- Scenario: append L1, L2, L3 to `out.txt`, 2 calls per step
- Reference run: 22 events, 3 side effects, 3 model requests
- Crash points: 25, each killed with SIGKILL (returncode -9 enforced by the driver)

## Result

Across 25 crash points, the three-state repair produced 0 duplicated and 0 missing side effects, and 25 of 25 resumes finished cleanly. The naive baseline produced 6 duplicated side effects across 6 points and 0 missing, and 25 of 25 resumes finished cleanly. The three-state repair issued 59 model requests in total against 54 for the baseline, where an uninterrupted run takes 3. Shape check: the repair was clean at every point, and the baseline duplicated at exactly the points between a side effect and its result.

## Totals

| mode | points | duplicates | points with duplicates | missing | resume_ok | model requests |
|---|---|---|---|---|---|---|
| ours | 25 | 0 | 0 | 0 | 25 | 59 |
| naive | 25 | 6 | 6 | 0 | 25 | 54 |

## Per point

Each mode shows duplicates, missing, resume_ok, model requests in the resume.

| point | last event | file lines | ours dup | ours miss | ours ok | ours req | naive dup | naive miss | naive ok | naive req |
|---|---|---|---|---|---|---|---|---|---|---|
| after_event:0 | session_started | 0 | 0 | 0 | yes | 3 | 0 | 0 | yes | 3 |
| after_event:1 | user_message | 0 | 0 | 0 | yes | 3 | 0 | 0 | yes | 3 |
| after_event:2 | model_request_started | 0 | 0 | 0 | yes | 3 | 0 | 0 | yes | 3 |
| after_event:3 | item | 0 | 0 | 0 | yes | 3 | 0 | 0 | yes | 3 |
| after_event:4 | item | 0 | 0 | 0 | yes | 3 | 0 | 0 | yes | 3 |
| after_event:5 | model_request_finished | 0 | 0 | 0 | yes | 3 | 0 | 0 | yes | 3 |
| after_event:6 | tool_call_started | 0 | 0 | 0 | yes | 4 | 0 | 0 | yes | 3 |
| after_event:7 | tool_call_finished | 1 | 0 | 0 | yes | 3 | 1 | 0 | yes | 3 |
| after_event:8 | item | 1 | 0 | 0 | yes | 2 | 0 | 0 | yes | 2 |
| after_event:9 | tool_call_started | 1 | 0 | 0 | yes | 3 | 0 | 0 | yes | 2 |
| after_event:10 | tool_call_finished | 2 | 0 | 0 | yes | 3 | 1 | 0 | yes | 2 |
| after_event:11 | item | 2 | 0 | 0 | yes | 2 | 0 | 0 | yes | 2 |
| after_event:12 | model_request_started | 2 | 0 | 0 | yes | 2 | 0 | 0 | yes | 2 |
| after_event:13 | item | 2 | 0 | 0 | yes | 2 | 0 | 0 | yes | 2 |
| after_event:14 | model_request_finished | 2 | 0 | 0 | yes | 2 | 0 | 0 | yes | 2 |
| after_event:15 | tool_call_started | 2 | 0 | 0 | yes | 3 | 0 | 0 | yes | 2 |
| after_event:16 | tool_call_finished | 3 | 0 | 0 | yes | 2 | 1 | 0 | yes | 2 |
| after_event:17 | item | 3 | 0 | 0 | yes | 1 | 0 | 0 | yes | 1 |
| after_event:18 | model_request_started | 3 | 0 | 0 | yes | 1 | 0 | 0 | yes | 1 |
| after_event:19 | item | 3 | 0 | 0 | yes | 1 | 0 | 0 | yes | 1 |
| after_event:20 | model_request_finished | 3 | 0 | 0 | yes | 1 | 0 | 0 | yes | 1 |
| after_event:21 | session_finished | 3 | 0 | 0 | yes | 1 | 0 | 0 | yes | 1 |
| after_effect:1 | tool_call_started | 1 | 0 | 0 | yes | 3 | 1 | 0 | yes | 3 |
| after_effect:2 | tool_call_started | 2 | 0 | 0 | yes | 3 | 1 | 0 | yes | 2 |
| after_effect:3 | tool_call_started | 3 | 0 | 0 | yes | 2 | 1 | 0 | yes | 2 |

## Method

Each point runs the scenario in a subprocess that SIGKILLs itself, either right after the log append for a given seq returns (`after_event:k`) or right after the shell exec that performed a given side effect (`after_effect:i`). The crashed directory (session log plus work dir) is copied byte for byte into two directories. One resumes with the three-state repair, the other with a naive baseline that marks every dangling call as not executed. Both use a fresh scenario provider and one `run(None)`. A resume that does not end `done`, leaves a dangling call, gives a call more than one result, or leaves a gap in the seqs is not `resume_ok`.

## Caveat

SIGKILL ends the process but does not drop the operating system page cache, so every byte written before the kill survives. This experiment therefore does not test fsync behavior under power loss or a kernel crash. It tests the repair rules against process death at every event boundary and after every side effect. The scenario provider is deterministic and goal-driven, so the numbers describe the repair logic, not model behavior.

## Interpretation

Written by hand after the report was generated. Regenerating the report overwrites this section, and the numbers above are untouched.

### What the numbers show

- The three-state repair produced 0 duplicates and 0 missing lines at all 25 crash points. The naive baseline duplicated one line at each of 6 points and lost none.
- Both arms reached `done` with every call resolved at 25 of 25 points. `resume_ok` therefore does not separate them. Duplicates are the metric that does.
- All six naive duplicates fall after the side effect and before its result is logged. Three land after `tool_call_finished` (`after_event:7`, `10` and `16`), where the only missing record is the result item. Three land right after the exec that wrote the line (`after_effect:1`, `2` and `3`), where the last logged event is still `tool_call_started`.
- A crash right after `tool_call_started` caused no duplicate in either arm when the effect had not run yet. `after_event:6`, `9` and `15` are those points, and the file holds only the lines from earlier calls.
- `after_event:6` and `after_effect:1` end on the same logged event, `tool_call_started`, and differ only in whether the line reached the file. The log cannot tell them apart. That is the reason a started mutating call is `unknown` and not `interrupted`.
- The shape check passed. The expected duplicate points are computed from the reference log, independently of the repair code, and they equal the observed set.

### The cost

The repair arm made 59 model requests against 54 for the baseline, 5 more across 25 points. Each of those is a verify read: after an `unknown` result the model issues `read_target` to check the file before it appends again. The gap sits at `after_event:6`, `9`, `10` and `15` and at `after_effect:2`. At the other four points where the baseline duplicated (`after_event:7` and `16`, `after_effect:1` and `3`) the counts are equal. Repair does not use the `tool_call_finished` event to skip the read. A finished call whose result was lost is still `unknown`, with the exit status added to the reason. Skipping the read there would be an optimization with a correctness argument to make, and it is not implemented.

### What this does not prove

- **Power loss.** SIGKILL keeps the page cache, so every written byte survives. The fsync policy under power loss or a kernel crash is untested. A real power loss could drop `tool_call_finished` and the result while `tool_call_started` survives, and the repair rules cover that state, but this run never produces it.
- **A real model.** The scenario provider is a deterministic policy. It reads `unknown`, verifies with a read, and then appends or moves on. The 0 duplicates is a joint result of the repair rules and a policy that follows "inspect before retrying". Whether an LLM follows that instruction is unmeasured. The baseline runs the same policy, so the comparison isolates the repair information, and the absolute numbers say nothing about model behavior.
- **Other scenarios.** There is one shape: three sequential appends, two per step, each a mutating call that runs alone, with a read tool as the only verifier. No parallel read-only batch, no `external` tool, no side effect that cannot be checked by reading, no crash during a resume, and no graceful interrupt.
- **Failure modes beyond duplication.** The baseline lost no lines, so duplication is its only failure mode here. Missing effects would need a repair that wrongly reports an unrun effect as done, and neither arm does that.
