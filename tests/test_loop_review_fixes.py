import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_helpers import (
    NoArgs,
    RigFactory,
    assert_every_call_resolved,
    assert_no_leaked_tasks,
    bodies,
    kinds,
    results,
    tool_calls,
)
from harness.agent.loop import Agent, AgentConfig
from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.cancel import CancelToken
from harness.core.events import (
    Event,
    EventBody,
    Interrupted,
    SessionFinished,
    SideEffect,
    SteerMessage,
)
from harness.env.local import LocalEnvironment
from harness.providers.base import (
    ItemDone,
    ModelCaps,
    ModelRequest,
    StreamEnd,
    StreamEvent,
)
from harness.providers.fake import DEFAULT_CAPS, FakeProvider, FakeTurn, call, say
from harness.store.base import SessionLog


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def _tool(name: str, side_effect: SideEffect, handler: object) -> Tool[NoArgs]:
    return Tool(
        name=name,
        description=name,
        args_model=NoArgs,
        side_effect=side_effect,
        handler=handler,  # pyright: ignore[reportArgumentType]
    )


async def _spontaneous_cancel(args: NoArgs, ctx: ToolContext) -> ToolOutput:
    inner: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    inner.cancel()
    await inner
    return ToolOutput(content="unreachable")


async def _raise_on_token(args: NoArgs, ctx: ToolContext) -> ToolOutput:
    await ctx.cancel.wait()
    raise asyncio.CancelledError


async def _timeout(args: NoArgs, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(content="partial", status="error", exit_status="timeout")


async def test_spontaneous_cancelled_error_in_a_handler_becomes_an_error_result(
    rigs: RigFactory,
) -> None:
    flaky = call("flaky")
    rig = rigs.make(
        [FakeTurn(items=(flaky,)), FakeTurn(items=(say("ok"),))],
        [_tool("flaky", "external", _spontaneous_cancel)],
    )
    outcome = await rig.agent.run("go")
    events = rig.events()
    assert outcome.status == "done"
    assert_every_call_resolved(events)
    [result] = results(events)
    assert result.status == "error"
    assert "CancelledError" in result.content


async def test_handler_raising_cancelled_error_on_the_token_is_closed_out(
    rigs: RigFactory,
) -> None:
    started = asyncio.Event()

    async def handler(args: NoArgs, ctx: ToolContext) -> ToolOutput:
        started.set()
        return await _raise_on_token(args, ctx)

    write = call("write")
    rig = rigs.make([FakeTurn(items=(write,))], [_tool("write", "mutating", handler)])
    token = CancelToken()
    task = asyncio.create_task(rig.agent.run("go", cancel=token))
    await started.wait()
    token.cancel()
    outcome = await task
    events = rig.events()
    assert outcome.status == "interrupted"
    assert_every_call_resolved(events)
    assert results(events)[0].status == "unknown"
    assert bodies(events, Interrupted) == [Interrupted(phase="tool", killed=(write.id,))]
    assert_no_leaked_tasks()


class _CancelRaisingProvider:
    name = "cancel-raising"

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.streaming = asyncio.Event()

    def capabilities(self, model: str) -> ModelCaps:
        return DEFAULT_CAPS

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncIterator[StreamEvent]:
        yield ItemDone(index=0, item=call("echo"))
        self.streaming.set()
        await cancel.wait()
        raise self.error


@pytest.mark.parametrize("error", [asyncio.CancelledError(), RuntimeError("stream closed")])
async def test_provider_failing_after_a_graceful_cancel_is_an_interrupt(
    rigs: RigFactory, tmp_path: Path, error: BaseException
) -> None:
    provider = _CancelRaisingProvider(error)
    log = rigs.open_log()
    agent = Agent(
        log=log,
        provider=provider,
        env=LocalEnvironment(tmp_path),
        tools=ToolRegistry([]),
        config=AgentConfig(model="m", system_prompt="s"),
    )
    token = CancelToken()
    task = asyncio.create_task(agent.run("go", cancel=token))
    await provider.streaming.wait()
    token.cancel()
    outcome = await task
    events = log.read()
    assert outcome.status == "interrupted"
    assert "error" not in kinds(events)
    assert bodies(events, Interrupted) == [Interrupted(phase="streaming")]
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


@pytest.mark.parametrize(
    ("side_effect", "status"), [("mutating", "unknown"), ("read_only", "error")]
)
async def test_timeout_status_depends_on_side_effect(
    rigs: RigFactory, side_effect: SideEffect, status: str
) -> None:
    slow = call("slow")
    rig = rigs.make(
        [FakeTurn(items=(slow,)), FakeTurn(items=(say("ok"),))],
        [_tool("slow", side_effect, _timeout)],
    )
    await rig.agent.run("go")
    [result] = results(rig.events())
    assert result.status == status
    assert result.content.startswith("partial")
    if side_effect == "mutating":
        assert "timeout" in result.content


@pytest.mark.parametrize(
    "field", [{"max_parallel_tools": 0}, {"tool_timeout_s": 0}, {"max_tool_output_bytes": 0}]
)
def test_config_rejects_values_that_would_hang_or_break(field: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(model="m", system_prompt="s", **field)  # pyright: ignore[reportArgumentType]


class _RecordingLog:
    def __init__(self, inner: SessionLog) -> None:
        self.inner = inner
        self.durable: dict[str, bool] = {}

    @property
    def session_id(self) -> str:
        return self.inner.session_id

    @property
    def next_seq(self) -> int:
        return self.inner.next_seq

    def append(self, body: EventBody, *, durable: bool = False) -> Event:
        self.durable.setdefault(body.type, durable)
        return self.inner.append(body, durable=durable)

    def read(self, from_seq: int = 0) -> list[Event]:
        return self.inner.read(from_seq)

    def close(self) -> None:
        self.inner.close()


async def test_write_ahead_asks_the_log_for_durability(rigs: RigFactory, tmp_path: Path) -> None:
    async def ok(args: NoArgs, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(content="ok")

    log = _RecordingLog(rigs.open_log())
    agent = Agent(
        log=log,
        provider=FakeProvider([FakeTurn(items=(call("t"),)), FakeTurn(items=(say("x"),))]),
        env=LocalEnvironment(tmp_path),
        tools=ToolRegistry([_tool("t", "mutating", ok)]),
        config=AgentConfig(model="m", system_prompt="s", mode="yolo"),
    )
    await agent.run("go")
    assert log.durable["tool_call_started"] is True


async def test_steering_left_over_from_an_interrupted_run_stays_in_that_run(
    rigs: RigFactory,
) -> None:
    started = asyncio.Event()

    async def block(args: NoArgs, ctx: ToolContext) -> ToolOutput:
        started.set()
        await ctx.cancel.wait()
        return ToolOutput(content="", status="error", exit_status="killed")

    rig = rigs.make([FakeTurn(items=(call("block"),))], [_tool("block", "read_only", block)])
    token = CancelToken()
    task = asyncio.create_task(rig.agent.run("go", cancel=token))
    await started.wait()
    rig.agent.steer("also do X")
    token.cancel()
    await task
    events = rig.events()
    steer_at = [e.seq for e in events if isinstance(e.body, SteerMessage)]
    finished_at = [e.seq for e in events if isinstance(e.body, SessionFinished)]
    assert steer_at and steer_at[0] < finished_at[0]
    assert_no_leaked_tasks()


class _TrailingProvider:
    name = "trailing"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self, model: str) -> ModelCaps:
        return DEFAULT_CAPS

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        yield ItemDone(index=0, item=say("done"))
        yield StreamEnd(stop_reason="end_turn")
        yield ItemDone(index=1, item=call("late"))
        raise RuntimeError("after end")


async def test_events_after_stream_end_are_ignored(rigs: RigFactory, tmp_path: Path) -> None:
    log = rigs.open_log()
    agent = Agent(
        log=log,
        provider=_TrailingProvider(),
        env=LocalEnvironment(tmp_path),
        tools=ToolRegistry([]),
        config=AgentConfig(model="m", system_prompt="s"),
    )
    outcome = await agent.run("go")
    events = log.read()
    assert outcome.status == "done"
    assert tool_calls(events) == []
    assert "error" not in kinds(events)

