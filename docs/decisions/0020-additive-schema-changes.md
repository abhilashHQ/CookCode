# 0020. Additive schema changes and run provenance

Status: accepted (M2)
Date: 2026-10-26

## Context
M2 needs new facts in the log: which config each run used, where bash ended, which files an undo restored, and which mode and policy applied. Logs outlive code (0002), so each change has to read correctly on both sides: new code on old logs, and old code on new logs.

## Options considered
- Bump `SCHEMA_VERSION` and write a migration: correct for a breaking change. No migration hook exists yet, and nothing here breaks an existing shape.
- Put the new facts in existing events (for example, undo as a `user_message`): no schema change, but the log stops saying what happened and the data cannot be queried.
- Additive changes only: new event types and new optional fields with defaults. `SCHEMA_VERSION` stays 1.

## Decision
Additive only. M2 adds:
- Event `run_started(config, config_id)`, internal. It is appended on every `run()`, after `session_started` (new sessions only) and before repair. A resumed run with another model, tool set, limits, mode or policy is now visible, and `config_id` describes the run and not just the first one. Budgets stay per run (0012).
- Event `checkpoint_restored(checkpoint_ids, paths, turns)`, model layer, so the context builder renders it for the model (0017).
- `ToolCallFinished.cwd`, optional, default `None` (0019).
- `RunConfig.mode` (default `None`) and `RunConfig.policy` (default empty tuple), added in a review fix after `run_started` landed.

`approval_requested`, `approval_resolved` and `checkpoint_created` already existed from M0 and are unchanged.

**What an old reader does with new logs.**
- A new event type falls into `OtherEvent`, which keeps every field and the layer it was written with (0002). `run_started` is skipped without harm.
- An old context builder projects only the event types it knows. A `checkpoint_restored` event reaches it as an `OtherEvent`, so the old reader would not tell the model about an undo. Reading a log with an old binary is for inspection. Resuming one needs the new code.
- Fields added to a known type (`cwd`, `mode`, `policy`) are dropped in memory by an old reader. `fork` copies raw bytes, so they stay on disk.

**What new code does with old logs.** Missing fields take their defaults. A `RunConfig` from M1 loads with `mode=None` and an empty policy, and `None` means "not recorded", not "ask". A test loads such a config.

**`config_id` across the boundary.** `compute_config_id` hashes the dumped config, which now includes `mode` and `policy`. A run in M2 with settings identical to an M1 run gets a different id. Ids from before and after M2 are not comparable. The stored ids on old events are not recomputed.

## Consequences
- No migration code, and old sessions resume under new code.
- `tests/test_events.py` round-trips one instance of every event type and fails if a new type has no sample.
- The cost is permanent: `run_started` and the new fields stay in every future reader, and any change to their meaning is a breaking change.
- A known tag whose shape changes still fails validation (0002), so the rule "new optional fields and new types only" has to hold in review. No test enforces it.

## What would change it
The first change that cannot be additive: a field whose meaning changes, a new value inside a closed literal (a new `exit_status`), or a layer change. That bumps `SCHEMA_VERSION` and forces the read-time migration design deferred in 0002.
