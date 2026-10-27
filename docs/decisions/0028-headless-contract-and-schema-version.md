# 0028. The headless contract is the log, and its version is the event schema version

Status: accepted (M4)
Date: 2026-10-26

## Context
Flagship 2 and Harbor-style runners drive the agent without a terminal (spec section 8). They need a stream of what happened, a way to answer approvals, exit codes, and a schema they can pin. The open gate question was the schema version policy.

## Options considered
- A separate public event schema mapped from the log: freedom to rename, but a second schema to version, a mapping to keep in sync, and the stream would no longer equal what resume reads.
- The log events themselves, one JSON object per line: one schema, and a consumer sees exactly what `resume` and `export` see. Display deltas are not included.
- The log plus display deltas mixed into stdout: smoother streaming text, but stdout would stop being "the log, verbatim".

## Decision
`harness -p <prompt> --json` writes each event as the core appends it, through an `ObservedLog` sink (0026). The lines are `Event.model_dump_json()`, the same bytes the session file holds. `session_started` is first for a new session and `session_finished` is last. Without `--json`, `-p` prints only the final assistant message.

Schema version policy: the headless contract version is `SCHEMA_VERSION` (now 1), carried on every event. Additive changes (new event types, new optional fields) keep the version (0020), and consumers must ignore unknown types, which the `other` fallback already does on our side. A rename, removal or meaning change bumps the version and gets a new schema file. `cookcode schema` prints the JSON schema generated from the pydantic models. The checked-in copy at `docs/schema/events-v1.json` is guarded by a test, so any model change shows up as a diff a reviewer has to accept.

Approvals: `--policy <file>` loads `{"allow": ["tool:pattern"], "deny": [...]}` into the gate's existing `Policy`, for both clients. Calls the policy leaves at "ask" are answered by `--approvals stdin` or denied. With stdin, the consumer sees `approval_requested` on stdout and answers with one line `{"call_id", "decision", "reason", "rule"}`. An answer naming another call, invalid JSON, or EOF all deny with a reason in the log. Stdin is read on a daemon thread, so a pending read never blocks shutdown.

Exit codes are 0 done, 1 error, 2 budget exceeded, 130 interrupted. SIGINT and SIGTERM both cancel cooperatively, and a second signal hard-stops. Setup errors go to stderr, so stdout stays pure JSONL.

## Consequences
- No mapping layer: the stream, the session file and the export input are one format.
- Internal call ids appear on the wire (approval answers name them). They are stable for the life of a session, which is all a consumer needs.
- No token streaming in headless mode. A consumer that wants live text has to wait for the finished `item`.
- The version only moves on breaking changes, so consumers must treat unknown fields and event types as ignorable. That is the price of additive evolution.

## What would change it
A consumer that needs live deltas (an interactive Flagship 2 UI), or a breaking change to an event, which would cut `events-v2.json` and keep v1 readable.
