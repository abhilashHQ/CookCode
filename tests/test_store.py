import errno
import json
import os
from pathlib import Path

import pytest

from harness.core.events import ToolCallStarted, UserMessage
from harness.redact import Redactor
from harness.store.base import (
    SessionCorruptError,
    SessionLockedError,
    SessionNotFoundError,
)
from harness.store.jsonl import JsonlSessionStore
from sample_events import all_bodies


def test_append_assigns_sequential_seq_and_reads_back(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        written = [log.append(UserMessage(text=f"m{i}")) for i in range(3)]
    assert [e.seq for e in written] == [0, 1, 2]
    assert store.read("s1") == written
    assert store.read("s1", from_seq=1) == written[1:]


def test_every_event_type_survives_the_store(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        written = [log.append(body) for body in all_bodies()]
    assert store.read("s1") == written


def test_open_missing_session_without_create_fails(tmp_path: Path) -> None:
    with pytest.raises(SessionNotFoundError):
        JsonlSessionStore(tmp_path).open("nope")


def test_reopen_continues_the_sequence(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text="one"))
    with store.open("s1") as log:
        assert log.next_seq == 1
        assert log.append(UserMessage(text="two")).seq == 1


def test_second_writer_is_refused_until_the_first_closes(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    first = store.open("s1", create=True)
    with pytest.raises(SessionLockedError):
        store.open("s1")
    first.close()
    store.open("s1").close()


def test_fsync_only_for_write_ahead_events_or_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("harness.store.jsonl.os.fsync", lambda fd: calls.append(fd))
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        calls.clear()
        log.append(UserMessage(text="no fsync"))
        assert calls == []
        log.append(
            ToolCallStarted(call_id="c1", tool="bash", arguments={}, side_effect="mutating")
        )
        assert len(calls) == 1
        log.append(UserMessage(text="forced"), durable=True)
        assert len(calls) == 2


def test_torn_tail_is_ignored_on_read_and_truncated_on_open(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text="one"))
    path = tmp_path / "s1.jsonl"
    with path.open("ab") as f:
        f.write(b'{"seq": 1, "id": "x", "ts"')
    assert [e.seq for e in store.read("s1")] == [0]
    with store.open("s1") as log:
        assert log.append(UserMessage(text="two")).seq == 1
    assert path.read_bytes().endswith(b"\n")
    assert store.read("s1")[1].body == UserMessage(text="two")


def test_secrets_never_reach_disk(tmp_path: Path) -> None:
    key = "sk-ant-" + "a1B2" * 10
    store = JsonlSessionStore(tmp_path, Redactor())
    with store.open("s1", create=True) as log:
        event = log.append(UserMessage(text=f"use {key}"))
    assert event.body == UserMessage(text="use [REDACTED:anthropic_key]")
    assert key not in (tmp_path / "s1.jsonl").read_text()


def test_append_after_close_fails(tmp_path: Path) -> None:
    log = JsonlSessionStore(tmp_path).open("s1", create=True)
    log.close()
    with pytest.raises(ValueError):
        log.append(UserMessage(text="late"))


def test_lock_file_descriptor_is_released_on_close(tmp_path: Path) -> None:
    before = len(os.listdir("/proc/self/fd"))
    log = JsonlSessionStore(tmp_path).open("s1", create=True)
    log.close()
    assert len(os.listdir("/proc/self/fd")) == before


def _seqs(path: Path) -> list[int]:
    return [json.loads(line)["seq"] for line in path.read_text().splitlines()]


def _fail_after_partial_write(fd: int, data: bytes) -> None:
    os.write(fd, data[:10])
    raise OSError(errno.ENOSPC, "No space left on device")


def test_failed_append_leaves_no_trace_and_does_not_consume_seq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "s1.jsonl"
    with JsonlSessionStore(tmp_path).open("s1", create=True) as log:
        log.append(UserMessage(text="a"))
        monkeypatch.setattr("harness.store.jsonl._write_all", _fail_after_partial_write)
        with pytest.raises(OSError):
            log.append(UserMessage(text="b"))
        monkeypatch.undo()
        assert log.append(UserMessage(text="c")).seq == 1
    assert _seqs(path) == [0, 1]
    assert [e.body for e in JsonlSessionStore(tmp_path).read("s1")] == [
        UserMessage(text="a"),
        UserMessage(text="c"),
    ]


def test_failed_fsync_rolls_back_the_durable_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_fsync(fd: int) -> None:
        raise OSError(errno.EIO, "I/O error")

    path = tmp_path / "s1.jsonl"
    with JsonlSessionStore(tmp_path).open("s1", create=True) as log:
        monkeypatch.setattr("harness.store.jsonl.os.fsync", broken_fsync)
        with pytest.raises(OSError):
            log.append(UserMessage(text="intent"), durable=True)
        monkeypatch.undo()
        assert log.append(UserMessage(text="next")).seq == 0
    assert _seqs(path) == [0]


def test_log_is_poisoned_when_rollback_fails_and_reopen_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_truncate(fd: int, length: int) -> None:
        raise OSError(errno.EIO, "I/O error")

    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text="a"))
        monkeypatch.setattr("harness.store.jsonl._write_all", _fail_after_partial_write)
        monkeypatch.setattr("harness.store.jsonl.os.ftruncate", broken_truncate)
        with pytest.raises(OSError):
            log.append(UserMessage(text="b"))
        monkeypatch.undo()
        with pytest.raises(ValueError, match="reopen"):
            log.append(UserMessage(text="c"))
    with store.open("s1") as log:
        assert log.append(UserMessage(text="c")).seq == 1
    assert _seqs(tmp_path / "s1.jsonl") == [0, 1]


def test_corrupt_line_raises_and_releases_the_lock(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text="a"))
    with (tmp_path / "s1.jsonl").open("ab") as f:
        f.write(b"not json\n")
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(2):
        with pytest.raises(SessionCorruptError, match="line 2"):
            store.open("s1")
    assert len(os.listdir("/proc/self/fd")) == before
    with pytest.raises(SessionCorruptError):
        store.read("s1")


def test_lone_surrogates_are_stored_escaped(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("s1", create=True) as log:
        log.append(UserMessage(text=os.fsdecode(b"ls file-\xff.txt")))
    assert store.read("s1")[0].body == UserMessage(text="ls file-\\udcff.txt")


def test_fork_copies_raw_bytes_including_fields_from_newer_schemas(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    with store.open("main", create=True) as log:
        log.append(UserMessage(text="a"))
    path = tmp_path / "main.jsonl"
    future = json.loads(path.read_text())
    future.update(seq=1, id="f1", future_envelope=1)
    future["body"]["future_body"] = 2
    with path.open("a") as f:
        f.write(json.dumps(future) + "\n")
    store.fork("main", 1, "branch")
    assert (tmp_path / "branch.jsonl").read_bytes() == path.read_bytes()
