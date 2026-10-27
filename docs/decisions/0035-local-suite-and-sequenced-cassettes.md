# 0035. Local suite: 24 fixture tasks, hidden checks, oracle solutions, sequenced cassettes

Status: accepted (M6 gate, 2026-10-26)
Date: 2026-10-26

## Context
Spec section 10 asks for 20 to 40 small tasks that run in minutes on every change against cassettes, and nightly against the real model. Two things needed deciding: what the suite holds, and what "against cassettes" means when cassettes from M3 (0024) match every request byte for byte. A suite cassette holds a whole agent run, so the system prompt, the tool schemas and every tool output are in its request bodies. One prompt edit would invalidate every cassette at once. The owner has no paid models, so every re-record spends free quota.

## Options considered
- Suite size: 20 (the floor), 24 in four kinds, or 32 with adversarial cases. 32 roughly doubles authoring time and the adversarial cases mostly test the model.
- Strict cassettes: any drift fails the run. Every prompt or tool change means re-recording all tasks live.
- Sequenced cassettes: responses replay in order without matching requests, and each request's first difference is reported as drift. A change to tools, the loop or a check still shows up in the result. A prompt change shows up as drift.
- No cassettes for the suite, oracle only: checks the tasks, not the harness.

## Decision
- 24 tasks, 6 each of bugfix, feature, refactor and investigate, in `evals/suite/<id>/`. Each has `task.toml` (kind, prompt, turn and wall-clock caps), `repo/`, a hidden `check.py` (`python check.py WORKDIR ANSWER_FILE`) and a `solution.sh` oracle. Checks embed their own tests, so a run that deletes or weakens the repo's tests fails. Investigate tasks are judged on the final reply, and the repo must stay unchanged.
- `cookcode eval run` copies a task's repo into a fresh git repo, runs the agent unattended (yolo, the task's caps), saves the final reply, runs the check, and writes `run.json` (suite hash, harness commit, provider, model, mode, attempts) plus one `results.jsonl` row per attempt.
- Modes: `oracle` (the solutions, no model), `replay` (the default, no network), `record` (live, saves each task's cassette), `live`.
- Suite cassettes are sequenced (`ReplayTransport(strict=False)`). Adapter cassettes stay strict, since there the request body is the thing under test.
- Work directories live at a fixed path (`$TMPDIR/harness-suite/<task>/<attempt>`), so the workspace path in the system prompt matches between record and replay. A recorded run replays with no drift (tested).

## Consequences
- Replay costs nothing and runs on every change. Drift says when the cassettes describe an older prompt. Re-recording is a deliberate, quota-spending step, never automatic.
- A sequenced replay can serve a response that no longer makes sense for the new request (e.g. a call to a renamed tool). The run then fails its check, which is the right signal: the harness changed in a way that matters, so re-record.
- Two suite runs on one machine at the same time would share work directories. Runs are serialized.
- The oracle run proves every task is solvable and every check can fail.

## What the first recording taught
- First recording, NIM MiniMax M3.1 Flash, one task at a time: 23 of 24 passed. It took 183 requests and 59 minutes, of which 58.5 minutes were model time and 21 s were tools. There were 116 429s even with one request in flight, all absorbed by the 0022 budget (at most 7 attempts).
- Seven runs hit their wall-clock cap (180 to 420 s). Five had already finished the work, and the one failure ran out of time while editing the tests. The cap measured provider latency and 429 waits, not the agent, and a run cut off by the clock replays differently, because replay is instant and asks for a request the tape does not have. Wall caps are now 900 s, as a guard against hangs only, and the turn cap is the quota guard. Those 7 tasks were re-recorded.
- Replay showed drift on almost every request. The cause was environment noise in tool output: `.git` object names (the fixture commit used the current time) and `ls -la` timestamps. `prepare` now commits with a fixed date and resets mtimes, so the fixture is byte- and time-identical on every run. Drift messages now show the text around the first differing character instead of a shared prefix.
- Replay of all 24 tasks takes about 35 s.

## What would change it
Re-records happening so often that drift stops meaning anything, or a sequenced replay passing a change it should have caught.
