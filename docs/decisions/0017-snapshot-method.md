# 0017. Snapshot method and undo

Status: proposed (M2 gate)
Date: 2026-10-26

## Context
A user who sees a bad edit wants the files back. The harness needs the old bytes before a mutating file call runs, a way to restore them, and a way to tell the model that its edits are gone. The spec sketched `Environment.snapshot`. The method also decides how the same code behaves against a local directory, a container and a remote sandbox.

## Options considered
- File copies read through the `Environment` and stored host-side by a `CheckpointStore`, content-addressed by sha256. The `Environment` needs no new method. Only files a tool declares through `touches` are covered.
- `Environment.snapshot(paths)` as in the spec sketch: each environment stores its own copies. Every environment must implement snapshot and restore, and the copies live wherever the environment lives.
- A git shadow repository: one commit per turn with a separate `GIT_DIR`. It covers every file in the tree, bash side effects included. It needs git in the environment, a rule for ignored and untracked files, and a full-tree walk each turn.

## Decision
**Gate:** Left for the gate.

Recommendation: file copies behind a `CheckpointStore`. The code implements it. Snapshotting is deliberately not an `Environment` method, a change from the spec sketch, so the environment protocol stays at exec, read, write and remove.

**Snapshot.** In `_run_call`, for a tool with `touches` and a configured store, the loop resolves the touched paths against the cwd the call will run in, reads each through `env.read_file` (only `FileNotFoundError` means "did not exist" and stores `None`), saves them under a fresh checkpoint id, and then appends `checkpoint_created(checkpoint_id, paths, turn)`. `turn` is the count of `user_message` events at that moment. Steering is a separate event type and does not shift it. Any failure gives the error result `snapshot failed: <Type>: <message>`, with no checkpoint event and no `tool_call_started`.

**Write-ahead order for a gated mutating file call:**
1. `approval_resolved`, when there was a decision to record
2. `checkpoint_created`
3. `tool_call_started`
4. the run
5. `tool_call_finished`
6. the result

A crash before step 3 leaves no side effect, and repair marks the call not executed (0011). A checkpoint with no `tool_call_started` after it holds bytes identical to the file, so restoring it changes nothing.

**Store.** `FileCheckpointStore` writes blobs to `root/blobs/<sha256>` once and one manifest per checkpoint mapping each path to its hash or null. Every write goes to a temporary file, is fsynced, and is moved into place with `os.replace`. Checkpoint ids must match `[A-Za-z0-9_-]+`, and `load` re-hashes every blob, so undo never restores corrupt bytes silently.

**Undo.** `undo(log=, env=, checkpoints=, turns=1)` is an async function called between runs, not a loop method. It ignores checkpoints already named by a `checkpoint_restored` event, takes the last `turns` distinct turn indexes, and for each path uses the earliest checkpoint in those turns. It loads every manifest before writing anything, writes the bytes back or calls `remove_file` for a file that did not exist, and then appends one `checkpoint_restored(checkpoint_ids, paths, turns)`. It returns `None` when nothing is left to undo.

**Undo is visible to the model.** `checkpoint_restored` is a model-layer event, and the context builder renders it as a user message: `[undo] Restored <paths> to their state before turn <n>. File edits made in turns <list> are gone.` The model is not left believing edits exist that were undone. Undo restores files only. Rewinding the conversation stays `fork`.

## Consequences
- Works the same against any environment that can read, write and remove a file.
- Which checkpoints are open is a projection of the log, so undo state survives a resume and a fresh store object.
- Bash side effects are not covered. A test documents it: `echo c1 > c.txt` in the same response as an edit is not restored.
- Snapshots are not redacted. Restoring needs exact bytes, so a blob can hold a secret that the event log would have removed (0004). The checkpoint directory is as sensitive as the workspace.
- Symlinks are followed on write and not detected. A path that is a link out of the workspace is snapshotted and restored at its target.
- Undo can be partial. If a write fails halfway, earlier paths are already restored and nothing is logged. Running undo again writes the same bytes, so it is safe to retry.
- Nothing stops a caller from running undo while a run is active. The CLI must not.
- `SnapshotRef` exists in `store/base.py` and nothing uses it yet.
- A failed edit still creates a checkpoint, because the snapshot precedes the handler.

## What would change it
Evidence in M6 that models change files through bash (`sed -i`, formatters, `git checkout`) often enough that file-level undo is too narrow. That would make the git shadow repository worth its cost. A requirement to restore whole directories would do the same.
