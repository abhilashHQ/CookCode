import asyncio
import io
import json
import os
import signal
from pathlib import Path

import pytest

from harness.agent.approval import Risk
from harness.cli.app import main, parse_args
from harness.cli.headless import LineReader, StdinApprover, run_headless
from harness.core.cancel import CancelToken
from harness.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    Event,
    ItemEvent,
    SessionFinished,
    event_schema,
    from_json_line,
)
from harness.core.items import ToolCall, ToolResult
from harness.core.usage import Usage
from harness.providers.fake import FakeProvider, FakeTurn, call, say
from harness.store.jsonl import JsonlSessionStore

SCHEMA_FILE = Path(__file__).parent.parent / "docs" / "schema" / "events-v1.json"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GIT_DIR", raising=False)
    return work


def stored(tmp_path: Path) -> list[Event]:
    store = JsonlSessionStore(tmp_path / "home" / "sessions")
    (session,) = store.list_sessions()
    return store.read(session)


async def headless(
    argv: list[str], fake: FakeProvider, stdin: str = ""
) -> tuple[int, list[str]]:
    out = io.StringIO()
    code = await run_headless(
        parse_args(argv), provider=fake, stdin=io.StringIO(stdin), stdout=out
    )
    return code, out.getvalue().splitlines()


def write_call() -> ToolCall:
    return call("write", path="notes.txt", content="deploy friday\n")


async def test_json_mode_streams_the_log_verbatim(workspace: Path, tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            FakeTurn(items=(call("glob", pattern="*"),), usage=Usage(input=10, output=2)),
            FakeTurn(items=(say("nothing here"),), usage=Usage(input=12, output=3)),
        ]
    )
    code, lines = await headless(["-p", "look around", "--json"], fake)
    assert code == 0
    events = [from_json_line(line) for line in lines]
    assert events == stored(tmp_path)
    assert events[0].body.type == "session_started"
    assert isinstance(events[-1].body, SessionFinished) and events[-1].body.status == "done"
    assert [event.seq for event in events] == list(range(len(events)))


async def test_text_mode_prints_the_final_answer(workspace: Path) -> None:
    fake = FakeProvider([FakeTurn(items=(say("all done"),))])
    code, lines = await headless(["-p", "hi"], fake)
    assert (code, lines) == (0, ["all done"])


async def test_stdin_answer_allows_the_named_call(workspace: Path, tmp_path: Path) -> None:
    write = write_call()
    fake = FakeProvider([FakeTurn(items=(write,)), FakeTurn(items=(say("written"),))])
    answer = json.dumps({"call_id": write.id, "decision": "allow"})
    code, lines = await headless(
        ["-p", "save it", "--json", "--approvals", "stdin"], fake, answer + "\n"
    )
    assert code == 0
    kinds = [json.loads(line)["body"]["type"] for line in lines]
    assert kinds.index("approval_requested") < kinds.index("approval_resolved")
    assert (workspace / "notes.txt").read_text() == "deploy friday\n"


@pytest.mark.parametrize(
    ("stdin", "reason"),
    [
        ("", "stdin closed before an approval answer"),
        ("not json\n", "invalid approval answer"),
        ('{"call_id": "other", "decision": "allow"}\n', "approval answer names call other"),
    ],
)
async def test_bad_or_missing_answers_deny(
    workspace: Path, tmp_path: Path, stdin: str, reason: str
) -> None:
    fake = FakeProvider([FakeTurn(items=(write_call(),)), FakeTurn(items=(say("ok"),))])
    code, _ = await headless(["-p", "save it", "--approvals", "stdin"], fake, stdin)
    assert code == 0
    resolved = [e.body for e in stored(tmp_path) if isinstance(e.body, ApprovalResolved)]
    assert resolved[0].decision == "deny"
    assert reason in (resolved[0].reason or "")
    assert not (workspace / "notes.txt").exists()


async def test_without_an_answer_source_open_approvals_deny(
    workspace: Path, tmp_path: Path
) -> None:
    fake = FakeProvider([FakeTurn(items=(write_call(),)), FakeTurn(items=(say("ok"),))])
    code, _ = await headless(["-p", "save it"], fake)
    assert code == 0
    events = stored(tmp_path)
    assert any(isinstance(e.body, ApprovalRequested) for e in events)
    (result,) = [
        e.body.item
        for e in events
        if isinstance(e.body, ItemEvent) and isinstance(e.body.item, ToolResult)
    ]
    assert result.status == "denied"


async def test_policy_file_allows_without_asking(workspace: Path, tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"allow": ["write:notes.txt"]}))
    fake = FakeProvider([FakeTurn(items=(write_call(),)), FakeTurn(items=(say("ok"),))])
    code, _ = await headless(["-p", "save it", "--policy", str(policy)], fake)
    assert code == 0
    events = stored(tmp_path)
    assert not any(isinstance(e.body, ApprovalRequested) for e in events)
    assert "allow write:notes.txt" in events[0].body.config.policy  # type: ignore[union-attr]
    assert (workspace / "notes.txt").exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"allow": ["no-colon"]}', "is not in the form tool:pattern"),
        ('{"allow": [], "extra": 1}', "cannot load policy"),
        ("{not json", "cannot load policy"),
    ],
)
async def test_bad_policy_file_exits_1(
    workspace: Path,
    tmp_path: Path,
    content: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(content)
    code, lines = await headless(["-p", "x", "--policy", str(policy)], FakeProvider([]))
    assert (code, lines) == (1, [])
    assert message in capsys.readouterr().err


async def test_missing_key_goes_to_stderr(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    out = io.StringIO()
    assert await run_headless(parse_args(["-p", "hi", "--json"]), stdout=out) == 1
    assert out.getvalue() == ""
    assert "set NVIDIA_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_signal_interrupts_with_130(workspace: Path, sig: signal.Signals) -> None:
    fake = FakeProvider([FakeTurn(items=(say("x" * 400),))], chunk_size=1, event_delay_s=0.02)

    async def interrupt() -> None:
        while not fake.requests:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        os.kill(os.getpid(), sig)

    sender = asyncio.create_task(interrupt())
    async with asyncio.timeout(10):
        code, lines = await headless(["-p", "talk", "--json"], fake)
    await sender
    assert code == 130
    last = from_json_line(lines[-1]).body
    assert isinstance(last, SessionFinished) and last.status == "interrupted"


async def test_approver_reads_one_line_per_decision() -> None:
    reader = LineReader(io.StringIO('{"call_id": "a", "decision": "allow_always", "rule": "r"}\n'))
    approver = StdinApprover(reader)
    risk = Risk(side_effect="mutating", rule=None, reason="r", cwd=".", subjects=())
    first = ToolCall(id="a", provider_call_id="p", name="write", arguments={})
    second = ToolCall(id="b", provider_call_id="q", name="write", arguments={})
    decision = await approver.decide(first, risk, CancelToken())
    assert (decision.action, decision.rule) == ("allow_always", "r")
    assert (await approver.decide(second, risk, CancelToken())).action == "deny"


def test_checked_in_schema_matches_the_models() -> None:
    expected = json.loads(SCHEMA_FILE.read_text())
    assert expected == event_schema(), "run: uv run harness schema > docs/schema/events-v1.json"


def test_schema_and_flag_errors_from_main(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["$id"] == "harness-events-v1"
    assert main(["--json", "hi"]) == 1
