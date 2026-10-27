# 0034. Resume, fork and replay are client commands over the log; replay verifies the builder

Status: accepted (M5)
Date: 2026-10-26

## Context
The core could already resume: `Agent.run` on a non-empty log runs the repair pass (spec section 6) and continues. What was missing was a way to reach that from a client, a way to branch a conversation, and evidence for the claim that the context builder is a pure function of the log (spec section 7). Before M5 nothing in the log let us check that claim.

## Options considered
- Store each request's full body in the log: replay would be trivial, but the log would grow by the whole history on every request (quadratic), and the system prompt would be duplicated.
- Hash the whole request: the log has the system prompt only as a hash and the tool schemas only as names, so a later process could never rebuild the request to compare.
- Hash only the history part of the request: `items_sha256` on `model_request_started`. The system prompt and tools are already pinned by the run config (`system_prompt_sha256`, tool names, `config_id`).

## Decision
- `cookcode resume <id>` (or `--resume`) opens the existing log and its checkpoint directory. The next run's prologue repairs dangling calls.
- Provider and model resolve in this order: flag, then the session's last recorded config, then environment, then preset. A model carries over only while its provider does, because a NIM model id means nothing to Gemini.
- `--continue` runs `Agent.run(None)`, finishing an interrupted turn without a new message. That matches spec section 6: an interrupted turn stays alone unless the user continues it.
- `cookcode fork <id> [--at SEQ]` copies the log prefix into a new session. It may cut between a call and its result. The fork's first run repairs that like a crash, which is the point: rewinding a conversation and recovering from a crash are the same operation.
- `cookcode sessions` lists sessions newest first.
- `cookcode replay <id> [--strategy raw]` rebuilds every request from the events before it, under the run config in force at that point, and compares `items_sha256`. Older logs without the hash count as `unverified`. Exit 1 on any mismatch. `raw` is the only strategy until M7.

## Consequences
- Every test that resumes a session also replays it, and all of them match. The builder's purity is checked on interrupted, crashed, forked, budget-stopped and provider-switched logs.
- Live: the Gemini fixture run (8 requests) and the NIM-to-Gemini switch (3 requests) both replayed as all `match`. The switch log also shows `reasoning_dropped=2` on the first Gemini request.
- M7 can compare compaction strategies by rebuilding requests from the same logs, which is what `--strategy` is for.
- The hash covers what the builder produced, not what the adapter sent. Adapter mapping is checked by cassettes (0024), so the two checks together cover the path from the log to the wire.

## What would change it
A strategy that depends on something outside the log (a summarizer model call, for example). Its output would then have to be logged as an event for replay to stay deterministic. M7 has to decide that.
