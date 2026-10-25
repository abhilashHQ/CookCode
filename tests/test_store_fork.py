from pathlib import Path

import pytest

from harness.core.events import UserMessage
from harness.store.jsonl import JsonlSessionStore


def _store_with_five(tmp_path: Path) -> JsonlSessionStore:
    store = JsonlSessionStore(tmp_path)
    with store.open("main", create=True) as log:
        for i in range(5):
            log.append(UserMessage(text=f"m{i}"))
    return store


def test_fork_copies_the_prefix_and_continues_independently(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    store.fork("main", 2, "branch")
    assert [e.seq for e in store.read("branch")] == [0, 1, 2]
    assert store.read("branch") == store.read("main")[:3]
    with store.open("branch") as log:
        assert log.append(UserMessage(text="alt")).seq == 3
    assert len(store.read("main")) == 5


def test_fork_at_a_missing_seq_fails(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    with pytest.raises(ValueError):
        store.fork("main", 99, "branch")


def test_fork_never_overwrites_an_existing_session(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    store.fork("main", 1, "branch")
    with pytest.raises(FileExistsError):
        store.fork("main", 2, "branch")
    assert [e.seq for e in store.read("branch")] == [0, 1]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".hidden", "x" * 129])
def test_session_ids_that_could_escape_the_root_are_rejected(tmp_path: Path, bad: str) -> None:
    store = _store_with_five(tmp_path)
    with pytest.raises(ValueError):
        store.open(bad, create=True)
    with pytest.raises(ValueError):
        store.fork("main", 1, bad)


def test_list_sessions_is_sorted_and_ignores_temp_files(tmp_path: Path) -> None:
    store = _store_with_five(tmp_path)
    store.fork("main", 0, "a-branch")
    (tmp_path / "half.jsonl.tmp").write_text("")
    assert store.list_sessions() == ["a-branch", "main"]
