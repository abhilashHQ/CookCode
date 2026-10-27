# 0042. Long-term memory: a notes arm and a facts arm with provenance and staleness on read

Status: accepted (M8 gate, 2026-10-27)
Date: 2026-10-27

## Context
Spec section 12a: an agent that works in one repository across sessions relearns the same things each time (how to run the tests, a codegen step, a house convention). Memory is a known way to fail quietly: a remembered fact goes stale when the code changes, and a confident wrong fact is worse than none. Memory also competes with prompt caching. Anything injected per turn changes the prefix.

## Options considered
- What to store: free-form notes the agent writes, atomic facts with provenance, or embeddings of past transcripts.
- When to write: the agent decides mid-session (a tool), or one consolidation pass at the end of the session.
- How to retrieve: inject everything, inject a fixed profile, search on demand, or inject per turn by similarity.
- Staleness: TTLs, re-validation by the model, or file hashes compared on read.
- Storage: JSON files, SQLite with FTS5, a vector store.

## Decision
- Two arms behind feature flags, so the ablation is none, notes and facts on the same tasks:
  - **notes:** `notes.md` per repository, appended to through a `remember` tool, shown at session start (capped at 6000 chars).
  - **facts:** `memory/store.py:FactStore`, SQLite plus FTS5, one database per repository.
- Repository identity is the root commit (`repo_key`), so every clone and fresh copy shares one memory.
- A fact carries its text, the files it cites with their content hashes at write time, and the commit. Relations: `updates` retires the fact it replaces (kept for history, no longer latest), while `extends` and `derives` only link.
- Writing (owner's call): one consolidation pass at the end of a run, through the `on_finish` hook. It makes a single side request (view `memory`) over the transcript and the existing facts, and returns at most 12 new facts as JSON lines. Facts are written with the hashes of the files they cite as they are at that moment.
- Reading (owner's call): a fixed profile of up to 20 fresh facts at session start, plus a separate `memory_search` tool (FTS5, 10 results). Nothing is injected per turn, so the prefix stays cacheable.
- Staleness is computed on read (`fresh`). A fact is stale when any file it cites no longer hashes as recorded. Stale facts are left out of the profile and the search results, and the count of hidden facts is shown. Staleness is never stored, so it can never disagree with the files.

## Consequences
- A fact about a file that changed disappears on its own. One that cites no files never goes stale, and that is the arm's weak point. The consolidation prompt asks for cited paths.
- The consolidation call costs one request per session, logged and skipped by replay. Mid-session `remember` costs a tool call each time, and the model decides when.
- FTS5 keyword search is enough at a repository's scale (dozens to hundreds of facts) and adds no dependency. Embeddings would add a model call per write and per search.
- Eval runs isolate memory per attempt (`out/memory/<attempt>`), so attempts never contaminate each other.

## What would change it
Facts piling up past what keyword search ranks well, or the ablation showing notes as good as facts (then keep the simpler one).
