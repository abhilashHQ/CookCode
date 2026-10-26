from harness.core.events import CheckpointCreated, CheckpointRestored
from harness.env.base import Environment
from harness.store.base import CheckpointStore, SessionLog


async def undo(
    *, log: SessionLog, env: Environment, checkpoints: CheckpointStore, turns: int = 1
) -> CheckpointRestored | None:
    if turns < 1:
        raise ValueError("turns must be at least 1")
    events = log.read()
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
    earliest: dict[str, str] = {}
    for checkpoint in selected:
        for path in checkpoint.paths:
            earliest.setdefault(path, checkpoint.checkpoint_id)
    loaded = {cid: checkpoints.load(cid) for cid in set(earliest.values())}
    contents = {path: loaded[cid][path] for path, cid in earliest.items()}
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
