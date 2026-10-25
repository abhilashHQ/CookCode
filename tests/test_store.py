import os
from pathlib import Path

import pytest

from harness.core.events import ToolCallStarted, UserMessage
from harness.redact import Redactor
from harness.store.base import SessionLockedError, SessionNotFoundError
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
