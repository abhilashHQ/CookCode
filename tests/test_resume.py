import asyncio
import io
import os
import signal
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_helpers import bodies, kinds, results
from cookcode.agent.approval import PolicyOnlyApprover
from cookcode.agent.repair import MUTATING_LOST, NOT_EXECUTED, READ_ONLY_LOST
from cookcode.cli.app import PRESETS, Runtime, main, parse_args, run_cli
from cookcode.cli.headless import run_headless
from cookcode.cli.terminal import Terminal
from cookcode.core.cancel import CancelToken
from cookcode.core.events import (
    BudgetExceeded,
    Event,
    Interrupted,
    Repaired,
    RunStarted,
    SessionFinished,
    ToolCallStarted,
    UserMessage,
)
from cookcode.core.items import Message
from cookcode.providers.fake import FakeProvider, FakeTurn, call, say
from cookcode.providers.openai_compat import OPENROUTER_BASE_URL
from session_helpers import (
    assert_replays,
    assert_valid,
    crashed_session,
    headless,
    make_workspace,
    only_session,
    store,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_workspace(tmp_path, monkeypatch)


def since_last_run(events: list[Event]) -> list[Event]:
    start = max(i for i, e in enumerate(events) if isinstance(e.body, RunStarted))
    return events[start:]


def answer(text: str) -> FakeProvider:
    return FakeProvider([FakeTurn(items=(say(text),))])


async def interrupted_run(argv: list[str], fake: FakeProvider, ready: Callable[[], bool]) -> int:
    async def interrupt() -> None:
        while not ready():
            await asyncio.sleep(0.005)
        os.kill(os.getpid(), signal.SIGINT)

    sender = asyncio.create_task(interrupt())
    async with asyncio.timeout(10):
        code = await headless(argv, fake)
    await sender
    return code


async def test_resume_after_interrupt_while_streaming(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slow = FakeProvider([FakeTurn(items=(say("x" * 400),))], chunk_size=1, event_delay_s=0.005)
    assert await interrupted_run(["-p", "talk"], slow, lambda: bool(slow.requests)) == 130
    session = only_session(tmp_path)
    assert bodies(store(tmp_path).read(session), Interrupted)[0].phase == "streaming"
    assert await headless(["--resume", session, "-p", "go on"], answer("back")) == 0
    events = store(tmp_path).read(session)
    assert_valid(events)
    assert bodies(since_last_run(events), UserMessage) == [UserMessage(text="go on")]
    assert_replays(session, capsys)


async def test_continue_after_interrupt_during_a_tool(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sleeper = call("bash", command="sleep 5")
    first = FakeProvider([FakeTurn(items=(sleeper,))])

    def tool_started() -> bool:
        sessions = store(tmp_path).list_sessions()
        return bool(sessions) and any(
            isinstance(e.body, ToolCallStarted) for e in store(tmp_path).read(sessions[0])
        )

    assert await interrupted_run(["-p", "wait", "--mode", "yolo"], first, tool_started) == 130
    session = only_session(tmp_path)
    assert bodies(store(tmp_path).read(session), Interrupted)[0].phase == "tool"
    fake = answer("it was cut short")
    assert await headless(["--resume", session, "--continue"], fake) == 0
    events = store(tmp_path).read(session)
    assert_valid(events)
    assert bodies(since_last_run(events), UserMessage) == []
    (result,) = results(events)
    assert result.call_id == sleeper.id
    assert fake.requests[0].items[-1] == result
    assert_replays(session, capsys)


async def test_resume_after_an_idle_interrupt(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = Runtime(parse_args(["-p", "hi"]), lambda event: None, FakeProvider([]))
    agent = await runtime.build_agent(PolicyOnlyApprover())
    cancel = CancelToken()
    cancel.cancel("user")
    assert (await agent.run("hi", cancel=cancel)).status == "interrupted"
    runtime.log.close()
    session = runtime.session_id
    assert bodies(store(tmp_path).read(session), Interrupted)[0].phase == "idle"
    assert await headless(["--resume", session, "-p", "hi again"], answer("hello")) == 0
    events = store(tmp_path).read(session)
    assert_valid(events)
    assert [m.text for m in bodies(events, UserMessage)] == ["hi", "hi again"]
    assert_replays(session, capsys)


@pytest.mark.parametrize(
    ("tool", "side_effect", "status", "reason"),
    [
        ("write", None, "interrupted", NOT_EXECUTED),
        ("read", "read_only", "interrupted", READ_ONLY_LOST),
        ("write", "mutating", "unknown", MUTATING_LOST),
    ],
)
async def test_resume_after_a_crash_repairs_the_dangling_call(
    workspace: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    side_effect: str | None,
    status: str,
    reason: str,
) -> None:
    dangling = call(tool, path="notes.txt", content="x\n")
    crashed_session(tmp_path, "crashed", dangling, side_effect)
    fake = answer("checked")
    assert await headless(["--resume", "crashed", "--continue"], fake) == 0
    events = store(tmp_path).read("crashed")
    assert_valid(events)
    (repaired,) = bodies(events, Repaired)
    assert (repaired.call_id, repaired.status, repaired.reason) == (dangling.id, status, reason)
    assert kinds(since_last_run(events))[:3] == ["run_started", "repaired", "item"]
    assert fake.requests[0].model == "fake-1"
    assert not (workspace / "notes.txt").exists()
    assert_replays("crashed", capsys)


async def test_budget_exceeded_then_resumed_with_more_turns(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = FakeProvider([FakeTurn(items=(call("glob", pattern="*"),))])
    assert await headless(["-p", "look", "--max-turns", "1"], first) == 2
    session = only_session(tmp_path)
    assert bodies(store(tmp_path).read(session), BudgetExceeded)[0].limit == "turns"
    resumed = ["--resume", session, "--continue", "--max-turns", "5"]
    assert await headless(resumed, answer("nothing here")) == 0
    events = store(tmp_path).read(session)
    assert_valid(events)
    assert bodies(events, RunStarted)[-1].config.limits.max_turns == 5
    assert_replays(session, capsys)


async def test_model_defaults_to_the_session_and_flags_override(
    workspace: Path, tmp_path: Path
) -> None:
    assert await headless(["-p", "one", "--model", "m1"], answer("a")) == 0
    session = only_session(tmp_path)
    inherited = answer("b")
    assert await headless(["--resume", session, "-p", "two"], inherited) == 0
    assert inherited.requests[0].model == "m1"
    explicit = answer("c")
    assert await headless(["--resume", session, "-p", "three", "--model", "m2"], explicit) == 0
    assert explicit.requests[0].model == "m2"
    models = [run.config.model for run in bodies(store(tmp_path).read(session), RunStarted)]
    assert models == ["m1", "m1", "m2"]


class OpenRouterFake(FakeProvider):
    name = "openrouter"


async def test_provider_defaults_to_the_session_and_flags_override(
    workspace: Path, tmp_path: Path
) -> None:
    first = OpenRouterFake([FakeTurn(items=(say("a"),))])
    assert await headless(["-p", "one", "--provider", "openrouter", "--model", "or-1"], first) == 0
    session = only_session(tmp_path)
    args = parse_args(["--resume", session, "-p", "two"])
    assert (args.provider, args.model) == ("nim", PRESETS["nim"][2])
    fake = OpenRouterFake([FakeTurn(items=(say("b"),))])
    assert await run_headless(args, provider=fake, stdout=io.StringIO()) == 0
    assert (args.provider, args.model, args.base_url) == ("openrouter", "or-1", OPENROUTER_BASE_URL)
    assert args.api_key_env == "OPENROUTER_API_KEY"
    override = parse_args(["--resume", session, "-p", "three", "--provider", "nim"])
    fake = OpenRouterFake([FakeTurn(items=(say("c"),))])
    assert await run_headless(override, provider=fake, stdout=io.StringIO()) == 0
    assert (override.provider, override.model) == ("nim", PRESETS["nim"][2])


async def test_interactive_resume_continues_the_same_log(workspace: Path, tmp_path: Path) -> None:
    assert await headless(["-p", "one"], answer("a")) == 0
    session = only_session(tmp_path)
    out = io.StringIO()
    fake = answer("b")
    code = await run_cli(
        parse_args(["--resume", session, "--once", "two"]),
        provider=fake,
        terminal=Terminal(out=out, stdin=io.StringIO()),
    )
    assert code == 0
    assert f"session {session} (resumed)" in out.getvalue()
    sent = [item.text for item in fake.requests[0].items if isinstance(item, Message)]
    assert sent == ["one", "a", "two"]
    assert len(bodies(store(tmp_path).read(session), SessionFinished)) == 2
    assert store(tmp_path).list_sessions() == [session]


async def test_unknown_or_locked_session_is_refused(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await headless(["--resume", "nope", "-p", "x"], FakeProvider([])) == 1
    assert "cookcode: no session nope" in capsys.readouterr().err
    assert await headless(["-p", "one"], answer("a")) == 0
    session = only_session(tmp_path)
    before = store(tmp_path).read(session)
    held = store(tmp_path).open(session)
    try:
        assert await headless(["--resume", session, "-p", "x"], FakeProvider([])) == 1
        assert "is open in another process" in capsys.readouterr().err
        code = await run_cli(
            parse_args(["--resume", session, "--once", "x"]),
            provider=FakeProvider([]),
            terminal=Terminal(out=io.StringIO(), stdin=io.StringIO()),
        )
        assert code == 1
        assert "is open in another process" in capsys.readouterr().err
    finally:
        held.close()
    assert store(tmp_path).read(session) == before


def test_continue_flag_rules(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--continue"])
    with pytest.raises(SystemExit):
        parse_args(["--resume", "s", "--continue", "-p", "x"])
    assert main(["resume", "nope", "--continue"]) == 1
    assert "no session nope" in capsys.readouterr().err
