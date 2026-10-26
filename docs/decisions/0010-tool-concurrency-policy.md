# 0010. Tool concurrency policy

Status: provisional, confirm at the M1 gate
Date: 2026-10-26

## Context
A response can hold several tool calls. Running them all at once is fast and lets writes race. Running them one at a time is safe and wastes time on independent reads. The log must also stay truthful, and the next request must not depend on timing.

## Options considered
- Always serial: simplest, slow for reads.
- All parallel: fastest, but the order of side effects is undefined.
- Parallel read-only runs, everything else alone, in call order: safe for writes, parallel where nothing changes.
- A per-tool "parallel safe" flag: more precise, but every tool author must declare it, and `side_effect` already covers the safe case.

## Decision
`_batches` splits the valid calls, in call order, into maximal runs of consecutive `read_only` calls and one single-call batch for each `mutating` or `external` call. Batches run in order, each in an `asyncio.TaskGroup`. Calls in a read-only batch run concurrently under a semaphore of `AgentConfig.max_parallel_tools`, default 8, created per dispatch. A mutating call waits for earlier reads, and later reads wait for it. Calls that fail validation are answered up front and skipped, so read, unknown, read is one batch.

Results are appended in completion order, right after each call finishes. `build_request` reorders each contiguous block of results into the order of the calls of the response it answers. That response is every call since the previous result block or user message, so text the model writes between two calls does not split it. The log records what happened, and the model sees one fixed order whatever the timing was.

**Gate item for the owner:** confirm the policy and the cap. The cap of 8 is a guess with no measurement behind it. Recommendation: keep both. Revisit the cap when M2 adds real tools and M6 gives timings.

## Consequences
- Result order in the log shows real concurrency, so result order does not equal call order.
- The reorder assumes a result block follows its call block directly. Steering only lands at step boundaries, so nothing is inserted between them.
- The class is trusted. A tool declared `read_only` that writes gets concurrency it should not have.
- `external` is serialized like `mutating`. No separate treatment yet.
- The cap applies within one step. There is no run-wide cap.
- Whether providers accept results in call order for every history, including repaired ones, is an open question in the spec (section 15), checked in M3.

## What would change it
Measured wall-time loss from serial writes, such as independent edits to different files, or a provider that requires completion order.
