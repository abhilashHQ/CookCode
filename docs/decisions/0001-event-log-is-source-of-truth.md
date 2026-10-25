# 0001. The event log is the source of truth

Status: accepted (M0)
Date: 2026-10-26

## Context
The harness needs one place that defines what happened in a session. Everything else (the context sent to the model, resume after a crash, replay for evals, exports, Flagship 2 checkpoints) must be derivable from it, or the copies drift apart.

## Options considered
- In-memory message list (the approach in pi and mini-swe-agent): simplest, but state dies with the process and there is nothing to replay or audit.
- One file per event (OpenHands): trivially append-safe, but a session becomes thousands of small files, and ordering and atomic reads get awkward.
- One JSONL file per session: append-only, readable with `grep` and `jq`, one writer, no schema migrations on a database.
- SQLite: good queries and transactions, but binary, needs migrations, and hides the data from plain tools.

## Decision
One append-only JSONL file per session, written through `JsonlSessionLog` in `src/harness/store/jsonl.py`. Each line is one `Event` (`src/harness/core/events.py`). Nothing else in the system holds authoritative session state.

## Consequences
- The model context, resume state, replay, exports and Flagship 2 checkpoints are all projections of the event list. A bug in a projection is fixed by recomputing it, not by repairing data.
- `JsonlSessionLog.open` reads the whole file to find the next `seq`, and `read` re-reads the file each call. Cost grows with session length.
- Cross-session questions ("which sessions used tool X") need a separate index later. The log alone answers nothing across files except by scanning them all.
- Forking is a prefix copy (`JsonlSessionStore.fork`), which only works because the log is complete and ordered.

## What would change it
Cross-session queries become a hot path, or session files grow large enough that a full read on open is too slow. The likely response is an index or a SQLite projection built from the logs, with the JSONL files staying the source of truth. Replacing the files themselves would need a stronger reason.
