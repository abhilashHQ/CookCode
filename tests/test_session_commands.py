from pathlib import Path

import pytest

from agent_helpers import bodies
from harness.cli.app import main
from harness.core.events import ItemEvent, ModelRequestStarted, Repaired
from harness.core.items import ToolCall
from harness.providers.fake import FakeProvider, FakeTurn, call, say
from session_helpers import (
    assert_replays,
    assert_valid,
    crashed_session,
    headless,
    make_workspace,
    only_session,
    replay,
    store,
    tamper,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_workspace(tmp_path, monkeypatch)


def run_main(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str], str]:
    capsys.readouterr()
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


async def read_session(tmp_path: Path) -> str:
    Path("notes.txt").write_text("deploy friday\n")
    fake = FakeProvider([
        FakeTurn(items=(call("read", path="notes.txt"),)),
        FakeTurn(items=(say("it says deploy friday"),)),
    ])
    assert await headless(["-p", "read the notes"], fake) == 0
    return only_session(tmp_path)


async def test_fork_mid_pair_then_resume(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = await read_session(tmp_path)
    events = store(tmp_path).read(source)
    cut = next(
        e.seq for e in events if isinstance(e.body, ItemEvent) and isinstance(e.body.item, ToolCall)
    )
    code, out, _ = run_main(["fork", source, "--at", str(cut)], capsys)
    assert code == 0
    (forked,) = out
    assert store(tmp_path).read(forked) == events[: cut + 1]
    fake = FakeProvider([FakeTurn(items=(say("read it again later"),))])
    assert await headless(["--resume", forked, "--continue"], fake) == 0
    resumed = store(tmp_path).read(forked)
    assert_valid(resumed)
    assert [r.status for r in bodies(resumed, Repaired)] == ["interrupted"]
    assert store(tmp_path).read(source) == events
    assert not (tmp_path / "home" / "checkpoints" / forked).exists()
    assert_replays(forked, capsys)
    assert_replays(source, capsys)


async def test_fork_defaults_to_the_last_seq(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = await read_session(tmp_path)
    code, out, _ = run_main(["fork", source], capsys)
    assert code == 0
    assert store(tmp_path).read(out[0]) == store(tmp_path).read(source)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["fork", "nope"], "no session nope"),
        (["fork", "nope", "--at", "3"], "no session nope"),
        (["fork", "bad/id"], "invalid session id"),
        (["replay", "nope"], "no session nope"),
    ],
)
def test_unknown_session_exits_1(
    workspace: Path, capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    code, out, err = run_main(argv, capsys)
    assert (code, out) == (1, [])
    assert message in err


async def test_fork_at_a_missing_seq_exits_1(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = await read_session(tmp_path)
    code, _, err = run_main(["fork", source, "--at", "9999"], capsys)
    assert code == 1
    assert "seq 9999 not found" in err


async def test_sessions_lists_newest_first_with_status(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    long_prompt = "explain " + "very " * 20 + "slowly"
    assert await headless(["-p", long_prompt], FakeProvider([FakeTurn(items=(say("a"),))])) == 0
    (done,) = store(tmp_path).list_sessions()
    budget = FakeProvider([FakeTurn(items=(call("glob", pattern="*"),))])
    assert await headless(["-p", "look", "--max-turns", "1"], budget) == 2
    (exceeded,) = set(store(tmp_path).list_sessions()) - {done}
    more = FakeProvider([FakeTurn(items=(say("b"),))])
    assert await headless(["--resume", done, "-p", "more"], more) == 0
    crashed_session(tmp_path, "zz-crashed", call("read", path="x"), "read_only")
    code, lines, _ = run_main(["sessions"], capsys)
    assert code == 0
    assert [line.split()[0] for line in lines] == ["zz-crashed", exceeded, done]
    crashed, over, finished = (line.split() for line in lines)
    assert crashed[3:7] == ["fake/fake-1", "open", "1", "runs"]
    assert over[3:7] == ["fake/minimaxai/minimax-m3.1-flash", "budget_exceeded", "1", "runs"]
    assert finished[4:6] == ["done", "2"]
    prompt = lines[2].split("runs  ", 1)[1]
    assert len(prompt) == 60 and prompt.endswith("...") and prompt.startswith("explain very")


def test_sessions_with_no_sessions_prints_nothing(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_main(["sessions"], capsys)[:2] == (0, [])


async def test_replay_matches_then_flags_a_tampered_hash(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = await read_session(tmp_path)
    code, lines = replay(session, capsys)
    assert code == 0
    requests = bodies(store(tmp_path).read(session), ModelRequestStarted)
    assert [line.split()[0] for line in lines[:-1]] == [r.request_id for r in requests]
    estimate = f"~{requests[0].token_estimate}"
    assert lines[0].split()[1:] == ["1", "items", estimate, "tokens", "match"]
    assert lines[1].split()[1] == "3"
    assert lines[-1] == "2 requests: 2 match, 0 mismatch, 0 unverified"
    tamper(tmp_path, session, nth=1)
    code, lines = replay(session, capsys)
    assert code == 1
    assert lines[0].endswith(" match") and lines[1].endswith(" MISMATCH")
    assert lines[-1] == "2 requests: 1 match, 1 mismatch, 0 unverified"


def test_replay_counts_old_logs_as_unverified(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    crashed_session(tmp_path, "old", call("read", path="x"), None)
    path = tmp_path / "home" / "sessions" / "old.jsonl"
    path.write_text(path.read_text().replace('"items_sha256":"', '"ignored":"'))
    code, lines = replay("old", capsys)
    assert code == 0
    assert lines == [lines[0], "1 requests: 0 match, 0 mismatch, 1 unverified"]
    assert lines[0].endswith(" unverified")


def test_replay_rejects_unknown_strategies(workspace: Path) -> None:
    with pytest.raises(SystemExit):
        main(["replay", "anything", "--strategy", "summary"])
