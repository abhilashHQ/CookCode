from cookcode.agent.repair import unresolved_calls
from cookcode.agent.tools import workspace_path
from cookcode.core.events import CheckpointCreated, CheckpointRestored
from cookcode.env.base import Environment
from cookcode.store.base import CheckpointStore, SessionLog

UNRESOLVED = "undo needs every tool call resolved; resume the session first"


async def undo(
    *, log: SessionLog, env: Environment, checkpoints: CheckpointStore, turns: int = 1
) -> CheckpointRestored | None:
    if turns < 1:
        raise ValueError("turns must be at least 1")
    events = log.read()
    if unresolved_calls(events):
        raise ValueError(UNRESOLVED)
    covered = {
        checkpoint_id
        for event in events
        if isinstance(event.body, CheckpointRestored)
        for checkpoint_id in event.body.checkpoint_ids
    }
    open_checkpoints = [
        event.body
        for event in events
        if isinstance(event.body, CheckpointCreated) and event.body.checkpoint_id not in covered
    ]
    picked = sorted({checkpoint.turn for checkpoint in open_checkpoints})[-turns:]
    selected = [checkpoint for checkpoint in open_checkpoints if checkpoint.turn in picked]
    if not selected:
        return None
    earliest: dict[str, tuple[str, str]] = {}
    for checkpoint in selected:
        for path in checkpoint.paths:
            canonical = workspace_path(env.root, ".", path)
            earliest.setdefault(canonical, (checkpoint.checkpoint_id, path))
    loaded = {cid: checkpoints.load(cid) for cid, _ in earliest.values()}
    contents = {canonical: loaded[cid][path] for canonical, (cid, path) in earliest.items()}
    for path, content in contents.items():
        if content is None:
            await env.remove_file(path)
        else:
            await env.write_file(path, content)
    restored = CheckpointRestored(
        checkpoint_ids=tuple(checkpoint.checkpoint_id for checkpoint in selected),
        paths=tuple(earliest),
        turns=tuple(picked),
    )
    log.append(restored)
    return restored
