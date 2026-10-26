import asyncio
import io
import os
import signal
from pathlib import Path

import pytest

from harness.agent.approval import Risk
from harness.cli.app import parse_args, run_cli
from harness.cli.terminal import (
    ObservedLog,
    Terminal,
    TerminalApprover,
    always_rule,
    call_diff,
    describe_call,
)
from harness.core.cancel import CancelToken
from harness.core.events import Event, UserMessage
from harness.core.items import ToolCall
from harness.providers.fake import FakeProvider, FakeTurn, call, say
from harness.store.jsonl import JsonlSessionStore


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GIT_DIR", raising=False)
    return work


def terminal(stdin: str = "") -> tuple[Terminal, io.StringIO]:
    out = io.StringIO()
    return Terminal(out=out, stdin=io.StringIO(stdin)), out


async def test_one_shot_run_shows_calls_results_and_status(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("deploy friday\n")
    (workspace / "AGENTS.md").write_text("Always be terse.\n")
    fake = FakeProvider([
        FakeTurn(items=(say("Reading."), call("read", path="notes.txt"))),
        FakeTurn(items=(say("It says deploy friday."),)),
    ])
    term, out = terminal()
    code = await run_cli(parse_args(["--once", "--mode", "yolo", "what do the notes say"]),
                         provider=fake, terminal=term)
    text = out.getvalue()
    assert code == 0
    assert "instructions: AGENTS.md" in text
    assert "> read(path=notes.txt)" in text
    assert "[ok] read(path=notes.txt):" in text and "deploy friday" in text
    assert "[done] 2 turns" in text
    assert "Always be terse." in fake.requests[0].system
    assert str(workspace) in fake.requests[0].system


async def test_ask_mode_takes_yes_and_no_from_the_terminal(workspace: Path) -> None:
    fake = FakeProvider([
        FakeTurn(items=(call("write", path="a.txt", content="one\n"),)),
        FakeTurn(items=(call("write", path="b.txt", content="two\n"),)),
        FakeTurn(items=(say("done"),)),
    ])
    term, out = terminal("y\nn\nnot that file\n")
    code = await run_cli(parse_args(["--once", "write files"]), provider=fake, terminal=term)
    assert code == 0
    assert (workspace / "a.txt").read_text() == "one\n"
    assert not (workspace / "b.txt").exists()
    assert "denied: not that file" in fake.requests[2].model_dump_json()
    assert "allow? [y]es, [n]o, [a]lways (write:a.txt)" in out.getvalue()
    assert "  touches a.txt (cwd .)" in out.getvalue()


async def test_undo_command_restores_the_last_turn(workspace: Path) -> None:
    (workspace / "a.txt").write_text("before\n")
    fake = FakeProvider([
        FakeTurn(items=(call("write", path="a.txt", content="after\n"),)),
        FakeTurn(items=(say("done"),)),
    ])
    term, out = terminal("edit it\n/undo\n/exit\n")
    code = await run_cli(parse_args(["--mode", "yolo"]), provider=fake, terminal=term)
    assert code == 0
    assert (workspace / "a.txt").read_text() == "before\n"
    assert "[undo] restored a.txt" in out.getvalue()


async def test_ctrl_c_interrupts_the_run_and_exits_130(workspace: Path) -> None:
    fake = FakeProvider([FakeTurn(items=(say("x" * 400),))], chunk_size=1, event_delay_s=0.01)
    term, out = terminal()

    async def interrupt() -> None:
        while not fake.requests:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        os.kill(os.getpid(), signal.SIGINT)

    sender = asyncio.create_task(interrupt())
    async with asyncio.timeout(10):
        code = await run_cli(parse_args(["--once", "--mode", "yolo", "talk"]),
                             provider=fake, terminal=term)
    await sender
    assert code == 130
    assert "[interrupted during streaming]" in out.getvalue()


async def test_missing_api_key_is_reported(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert await run_cli(parse_args(["--once", "hi"])) == 1
    assert "set NVIDIA_API_KEY" in capsys.readouterr().out


def risk(*subjects: str) -> Risk:
    return Risk(side_effect="mutating", rule=None, reason="r", cwd=".", subjects=subjects)


async def test_approver_offers_always_with_a_narrow_rule() -> None:
    term, _ = terminal("maybe\na\n")
    bash = ToolCall(id="c", provider_call_id="p", name="bash", arguments={"command": "make"})
    decision = await TerminalApprover(term).decide(bash, risk(), CancelToken())
    assert decision.action == "allow_always" and decision.rule == "bash:make"


async def test_approver_denies_when_input_closes() -> None:
    term, _ = terminal("")
    write = ToolCall(id="c", provider_call_id="p", name="write", arguments={"path": "a"})
    decision = await TerminalApprover(term).decide(write, risk("a"), CancelToken())
    assert decision.action == "deny"


async def test_stdin_from_dev_null_reads_eof_without_a_selector_error() -> None:
    with open(os.devnull) as null:
        term = Terminal(out=io.StringIO(), stdin=null)
        await term.start()
        assert await term.readline("> ") is None
        term.close()


def test_provider_presets_fill_url_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HARNESS_PROVIDER", "HARNESS_MODEL", "HARNESS_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    nim = parse_args([])
    assert (nim.provider, nim.api_key_env, nim.model) == (
        "nim", "NVIDIA_API_KEY", "minimaxai/minimax-m3.1-flash"
    )
    assert nim.base_url == "https://integrate.api.nvidia.com/v1"
    router = parse_args(["--provider", "openrouter", "--model", "x/y"])
    assert (router.api_key_env, router.model) == ("OPENROUTER_API_KEY", "x/y")


def test_always_rule_needs_a_single_subject() -> None:
    edit = ToolCall(id="c", provider_call_id="p", name="edit", arguments={})
    assert always_rule(edit, risk("a.py")) == "edit:a.py"
    assert always_rule(edit, risk("a.py", "b.py")) is None


def test_calls_render_compactly_with_diffs_for_edits() -> None:
    edit = ToolCall(id="c", provider_call_id="p", name="edit",
                    arguments={"path": "a.py", "old_string": "x = 1\n", "new_string": "x = 2\n"})
    assert describe_call(edit) == "edit(path=a.py)"
    assert call_diff(edit) == ["@@ -1 +1 @@", "-x = 1", "+x = 2"]
    bad = ToolCall(id="c", provider_call_id="p", name="read", arguments={},
                   raw_arguments='{"path":')
    assert describe_call(bad) == "read(<invalid JSON: {\"path\":>)"


def test_a_failing_observer_never_breaks_an_append(tmp_path: Path) -> None:
    seen: list[Event] = []

    def sink(event: Event) -> None:
        seen.append(event)
        raise BrokenPipeError("stdout closed")

    with JsonlSessionStore(tmp_path).open("s", create=True) as inner:
        log = ObservedLog(inner, sink)
        log.append(UserMessage(text="one"))
        log.append(UserMessage(text="two"))
        assert [e.seq for e in inner.read()] == [0, 1]
    assert len(seen) == 1
