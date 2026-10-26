# 0029. ATIF is a projection of the log, keyed by provider call ids

Status: accepted (M4)
Date: 2026-10-26

## Context
Harbor reads trajectories in ATIF (spec section 10). Harbor 0.25.0 defaults to `ATIF-v1.8`, not the v1.7 the spec named. Every ATIF model sets `extra: forbid`, steps must be numbered from 1, and an observation result may only reference a `tool_call_id` from the same step. The spec's open question was which fields are required.

Required, from the models: top level `schema_version`, `agent` (`name`, `version`) and at least one step. Each step needs `step_id`, `source` (`system`, `user` or `agent`) and `message`. Agent-only fields (model name, reasoning, tool calls, metrics) are rejected on other sources.

## Options considered
- Write ATIF live from the loop: a second writer beside the log, which can disagree with it after a crash.
- Build it with harbor's pydantic models in the core: correct by construction, but the core would depend on harbor.
- A pure function from events to a plain dict, validated against harbor's models in tests.

## Decision
`export/atif.py:to_atif(events)`. One user step per `user_message` or `steer_message` (steers marked in `extra`). One agent step per model request: its text, its reasoning text, its tool calls, and metrics from `model_request_finished` (`prompt_tokens` includes cache, as ATIF defines it). Each tool result goes into the `observation` of the step that issued the call, even when a repair wrote it in a later run. Interrupts, budgets and errors become system steps. Retries, stop reason, approvals, exit status, duration and repair reasons go under `extra`.

The join key is the provider call id: `tool_call_id` and `source_call_id` are `provider_call_id`, and our internal id stays internal (spec section 10). Unparseable arguments go out as `{}`, with the raw text under `extra.raw_arguments`, because ATIF arguments must be an object.

`harness export <session> --format atif` and the Harbor adapter both call it. Tests validate its output with harbor's `Trajectory` model, so harbor is a dev dependency and an optional extra, never a core import.

## Consequences
- The trajectory can be rebuilt from the log at any time, including after a Harbor timeout cancelled the run.
- ATIF cannot hold our full log: there are no checkpoint or compaction events, and the system prompt is only a hash (`agent.extra.system_prompt_sha256`), because the log stores the hash, not the text. A trajectory is for evaluation, not for resume.
- A version bump in ATIF that renames a field breaks the tests before it breaks a run.

## What would change it
Harbor requiring the system prompt text or token ids for RL use, which would mean logging the prompt (or a sidecar) and the per-token data our adapter does not capture today.
