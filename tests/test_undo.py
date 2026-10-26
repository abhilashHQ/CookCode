from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import Rig, RigFactory, assert_every_call_resolved, bodies
from harness.agent.undo import undo
from harness.core.events import CheckpointCreated, CheckpointRestored
from harness.core.items import Message
from harness.env.local import LocalEnvironment
from harness.providers.base import ModelRequest
from harness.providers.fake import FakeTurn, call, say
from harness.store.checkpoints import FileCheckpointStore
from harness.tools.bash import bash_tool
from harness.tools.files import edit_tool, write_tool

UNDO_TURN_2 = (
    "[undo] Restored b.py to their state before turn 2. File edits made in turns 2 are gone."
)


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def _store(rigs: RigFactory) -> FileCheckpointStore:
    return FileCheckpointStore(rigs.root / "checkpoints")


def _last_user_text(request: ModelRequest) -> str:
    users = [i for i in request.items if isinstance(i, Message) and i.role == "user"]
    return users[-1].text


async def _two_turns(rigs: RigFactory, *extra: FakeTurn) -> Rig:
    (rigs.workdir / "a.py").write_text("v0\n")
    rig = rigs.make(
        [
            FakeTurn(items=(call("edit", path="a.py", old_string="v0", new_string="v1"),)),
            FakeTurn(items=(call("edit", path="a.py", old_string="v1", new_string="v2"),)),
            FakeTurn(items=(say("turn 1 done"),)),
            FakeTurn(items=(call("write", path="b.py", content="new\n"),)),
            FakeTurn(items=(say("turn 2 done"),)),
            *extra,
        ],
        [edit_tool(), write_tool()],
        mode="auto-edit",
        checkpoints=_store(rigs),
    )
    await rig.agent.run("turn 1")
    await rig.agent.run("turn 2")
    return rig


async def test_undo_walks_back_one_turn_at_a_time(rigs: RigFactory) -> None:
    rig = await _two_turns(rigs)
    env = LocalEnvironment(rig.workdir)
    store = _store(rigs)
    created = bodies(rig.events(), CheckpointCreated)
    assert [c.turn for c in created] == [1, 1, 2]

    first = await undo(log=rig.log, env=env, checkpoints=store)

    assert first == CheckpointRestored(
        checkpoint_ids=(created[2].checkpoint_id,), paths=("b.py",), turns=(2,)
    )
    assert not (rig.workdir / "b.py").exists()
    assert (rig.workdir / "a.py").read_text() == "v2\n"

    second = await undo(log=rig.log, env=env, checkpoints=store)

    assert second == CheckpointRestored(
        checkpoint_ids=(created[0].checkpoint_id, created[1].checkpoint_id),
        paths=("a.py",),
        turns=(1,),
    )
    assert (rig.workdir / "a.py").read_text() == "v0\n"

    assert await undo(log=rig.log, env=env, checkpoints=store) is None
    assert bodies(rig.events(), CheckpointRestored) == [first, second]
    assert_every_call_resolved(rig.events())


async def test_undo_of_several_turns_restores_the_earliest_state(rigs: RigFactory) -> None:
    rig = await _two_turns(rigs)

    restored = await undo(
        log=rig.log, env=LocalEnvironment(rig.workdir), checkpoints=_store(rigs), turns=2
    )

    assert restored is not None
    assert restored.turns == (1, 2)
    assert restored.paths == ("a.py", "b.py")
    assert (rig.workdir / "a.py").read_text() == "v0\n"
    assert not (rig.workdir / "b.py").exists()


async def test_undo_is_visible_to_the_model(rigs: RigFactory) -> None:
    rig = await _two_turns(rigs, FakeTurn(items=(say("noted"),)))

    await undo(log=rig.log, env=LocalEnvironment(rig.workdir), checkpoints=_store(rigs))
    await rig.agent.run()

    assert _last_user_text(rig.provider.requests[-1]) == UNDO_TURN_2


async def test_bash_side_effects_are_not_covered(rigs: RigFactory) -> None:
    (rigs.workdir / "a.py").write_text("v0\n")
    (rigs.workdir / "c.txt").write_text("c0\n")
    rig = rigs.make(
        [
            FakeTurn(
                items=(
                    call("edit", path="a.py", old_string="v0", new_string="v1"),
                    call("bash", command="echo c1 > c.txt"),
                )
            ),
            FakeTurn(items=(say("ok"),)),
        ],
        [edit_tool(), bash_tool()],
        mode="yolo",
        checkpoints=_store(rigs),
    )
    await rig.agent.run("go")
    assert (rig.workdir / "c.txt").read_text() == "c1\n"

    restored = await undo(
        log=rig.log, env=LocalEnvironment(rig.workdir), checkpoints=_store(rigs)
    )

    assert restored is not None and restored.paths == ("a.py",)
    assert (rig.workdir / "a.py").read_text() == "v0\n"
    assert (rig.workdir / "c.txt").read_text() == "c1\n"


async def test_without_a_store_nothing_is_snapshotted_and_undo_is_a_no_op(
    rigs: RigFactory,
) -> None:
    rig = rigs.make(
        [FakeTurn(items=(call("write", path="b.py", content="x"),)), FakeTurn(items=(say("ok"),))],
        [write_tool()],
    )
    await rig.agent.run("go")

    restored = await undo(
        log=rig.log, env=LocalEnvironment(rig.workdir), checkpoints=_store(rigs)
    )

    assert bodies(rig.events(), CheckpointCreated) == []
    assert restored is None
    assert (rig.workdir / "b.py").read_text() == "x"


async def test_undo_works_on_a_reopened_log_with_a_fresh_store(rigs: RigFactory) -> None:
    rig = await _two_turns(rigs)
    rig.log.close()
    log = rigs.open_log()

    restored = await undo(
        log=log, env=LocalEnvironment(rigs.workdir), checkpoints=_store(rigs)
    )

    assert restored is not None and restored.turns == (2,)
    assert not (rigs.workdir / "b.py").exists()
    fresh = rigs.make(
        [
            FakeTurn(items=(call("write", path="b.py", content="again\n"),)),
            FakeTurn(items=(say("ok"),)),
        ],
        [write_tool()],
        log=log,
        mode="auto-edit",
        checkpoints=_store(rigs),
    )
    await fresh.agent.run("turn 3")

    assert _last_user_text(fresh.provider.requests[0]) == "turn 3"
    assert bodies(fresh.events(), CheckpointCreated)[-1].turn == 3
    again = await undo(log=log, env=LocalEnvironment(rigs.workdir), checkpoints=_store(rigs))
    assert again is not None and again.turns == (3,)
    assert not (rigs.workdir / "b.py").exists()
    assert (rigs.workdir / "a.py").read_text() == "v2\n"
