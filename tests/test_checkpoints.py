import hashlib
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from agent_helpers import (
    RigFactory,
    assert_every_call_resolved,
    bodies,
    echo_tool,
    kinds,
    results,
)
from harness.core.events import CheckpointCreated, ToolCallStarted
from harness.core.items import ToolResult
from harness.providers.fake import FakeTurn, call, say
from harness.store.checkpoints import FileCheckpointStore
from harness.tools.files import edit_tool, read_tool, write_tool


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_round_trip_keeps_content_and_missing_files(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path / "cp")

    store.save("c1", {"a.py": b"one\n", "sub/gone.py": None, "empty": b""})

    assert store.load("c1") == {"a.py": b"one\n", "sub/gone.py": None, "empty": b""}


def test_identical_content_is_stored_once(tmp_path: Path) -> None:
    root = tmp_path / "cp"
    store = FileCheckpointStore(root)

    store.save("c1", {"a.py": b"same", "b.py": b"same"})
    store.save("c2", {"a.py": b"same", "c.py": b"other"})

    assert sorted(p.name for p in (root / "blobs").iterdir()) == sorted(
        [_sha(b"same"), _sha(b"other")]
    )


def test_manifests_survive_a_reopen_and_no_temp_files_remain(tmp_path: Path) -> None:
    root = tmp_path / "cp"
    FileCheckpointStore(root).save("c1", {"a.py": b"x", "b.py": None})

    reopened = FileCheckpointStore(root)

    assert reopened.load("c1") == {"a.py": b"x", "b.py": None}
    leftovers = [p for p in root.rglob("*") if p.is_file() and p.suffix == ".tmp"]
    assert leftovers == []
    assert (root / "c1.json").is_file()


@pytest.mark.parametrize("checkpoint_id", ["", "../x", "a/b", "a.b"])
def test_unsafe_checkpoint_ids_are_rejected(tmp_path: Path, checkpoint_id: str) -> None:
    store = FileCheckpointStore(tmp_path / "cp")

    with pytest.raises(ValueError, match="invalid checkpoint id"):
        store.save(checkpoint_id, {})
    with pytest.raises(ValueError, match="invalid checkpoint id"):
        store.load(checkpoint_id)


def test_a_corrupted_blob_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "cp"
    store = FileCheckpointStore(root)
    store.save("c1", {"a.py": b"good"})
    (root / "blobs" / _sha(b"good")).write_bytes(b"evil")

    with pytest.raises(ValueError, match="corrupt"):
        store.load("c1")


async def test_an_edit_logs_approval_then_checkpoint_then_start(rigs: RigFactory) -> None:
    (rigs.workdir / "a.py").write_text("before\n")
    edit = call("edit", path="a.py", old_string="before", new_string="after")
    store = FileCheckpointStore(rigs.root / "checkpoints")
    rig = rigs.make(
        [FakeTurn(items=(edit,)), FakeTurn(items=(say("ok"),))],
        [edit_tool()],
        mode="auto-edit",
        checkpoints=store,
    )

    await rig.agent.run("go")

    events = rig.events()
    sequence = kinds(events)
    at = sequence.index("checkpoint_created")
    assert sequence[at - 1 : at + 3] == [
        "approval_resolved",
        "checkpoint_created",
        "tool_call_started",
        "tool_call_finished",
    ]
    [checkpoint] = bodies(events, CheckpointCreated)
    assert checkpoint.paths == ("a.py",)
    assert checkpoint.turn == 1
    assert store.load(checkpoint.checkpoint_id) == {"a.py": b"before\n"}
    assert (rig.workdir / "a.py").read_text() == "after\n"
    assert_every_call_resolved(events)


async def test_paths_are_normalized_against_the_cwd_and_missing_files_are_none(
    rigs: RigFactory,
) -> None:
    (rigs.workdir / "sub").mkdir()
    write = call("write", path="./x/../new.py", content="hi")
    store = FileCheckpointStore(rigs.root / "checkpoints")
    rig = rigs.make(
        [FakeTurn(items=(write,)), FakeTurn(items=(say("ok"),))],
        [write_tool()],
        cwd="sub",
        checkpoints=store,
    )

    await rig.agent.run("go")

    [checkpoint] = bodies(rig.events(), CheckpointCreated)
    assert checkpoint.paths == ("sub/new.py",)
    assert store.load(checkpoint.checkpoint_id) == {"sub/new.py": None}
    assert (rig.workdir / "sub" / "new.py").read_text() == "hi"


async def test_tools_without_touches_are_not_snapshotted(rigs: RigFactory) -> None:
    (rigs.workdir / "a.py").write_text("x\n")
    rig = rigs.make(
        [
            FakeTurn(items=(call("read", path="a.py"), call("echo", text="hi"))),
            FakeTurn(items=(say("ok"),)),
        ],
        [read_tool(), echo_tool()],
        checkpoints=FileCheckpointStore(rigs.root / "checkpoints"),
    )

    await rig.agent.run("go")

    assert bodies(rig.events(), CheckpointCreated) == []
    assert not (rigs.root / "checkpoints").exists()


class FailingStore:
    def save(self, checkpoint_id: str, files: Mapping[str, bytes | None]) -> None:
        raise OSError("disk full")

    def load(self, checkpoint_id: str) -> dict[str, bytes | None]:
        raise AssertionError("never loaded")


async def test_a_failed_snapshot_means_the_edit_never_runs(rigs: RigFactory) -> None:
    (rigs.workdir / "a.py").write_text("before\n")
    edit = call("edit", path="a.py", old_string="before", new_string="after")
    rig = rigs.make(
        [FakeTurn(items=(edit,)), FakeTurn(items=(say("ok"),))],
        [edit_tool()],
        mode="auto-edit",
        checkpoints=FailingStore(),
    )

    outcome = await rig.agent.run("go")

    events = rig.events()
    assert outcome.status == "done"
    assert (rig.workdir / "a.py").read_text() == "before\n"
    assert results(events) == [
        ToolResult(call_id=edit.id, status="error", content="snapshot failed: OSError: disk full")
    ]
    assert bodies(events, ToolCallStarted) == []
    assert bodies(events, CheckpointCreated) == []
    assert_every_call_resolved(events)


def test_save_fsyncs_the_blob_and_root_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    synced: list[str] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        synced.append(os.readlink(f"/proc/self/fd/{fd}"))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    root = tmp_path / "cps"
    FileCheckpointStore(root).save("c1", {"a.py": b"one", "gone.py": None})
    assert str(root / "blobs") in synced
    assert synced[-1] == str(root)
    assert synced.index(str(root / "blobs")) < synced.index(str(root))
