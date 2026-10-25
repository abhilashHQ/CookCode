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
