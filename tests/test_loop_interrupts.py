import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import (
    NoArgs,
    RigFactory,
    append_tool,
    assert_every_call_resolved,
    assert_no_leaked_tasks,
    bodies,
    echo_tool,
    items,
    kinds,
    results,
    tool_calls,
    wait_cancel_tool,
    wait_until,
)
from harness.agent.repair import NOT_EXECUTED
from harness.agent.tools import Tool, ToolContext, ToolOutput
from harness.core.cancel import CancelToken
from harness.core.events import (
    BudgetExceeded,
    Interrupted,
    Limits,
    ModelRequestFinished,
    SessionFinished,
    SideEffect,
    ToolCallFinished,
    ToolCallStarted,
)
from harness.core.items import Message, ToolResult
from harness.core.usage import Usage
from harness.providers.fake import FakeTurn, call, say


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


async def _cancel_when(event: asyncio.Event, token: CancelToken) -> None:
    await event.wait()
    token.cancel()


async def test_pre_cancelled_token_makes_no_request(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("never"),))])
    token = CancelToken()
    token.cancel()
    outcome = await rig.agent.run("hi", cancel=token)
    events = rig.events()
    assert rig.provider.requests == []
    assert kinds(events) == [
        "session_started",
        "run_started",
        "user_message",
        "interrupted",
        "session_finished",
    ]
    assert bodies(events, Interrupted) == [Interrupted(phase="idle")]
    assert outcome.status == "interrupted"
    assert outcome.turns == 0
    assert bodies(events, SessionFinished)[0].status == "interrupted"
    assert_no_leaked_tasks()


async def test_cancel_mid_stream_keeps_finished_items_and_closes_out_calls(
    rigs: RigFactory,
) -> None:
    first = call("echo", text="a")
    later = call("echo", text="b")
    rig = rigs.make(
        [FakeTurn(items=(first, say("a message long enough to stream in chunks"), later))],
        [echo_tool()],
        event_delay_s=0.02,
    )
    token = CancelToken()

    async def cancel_after_first_item() -> None:
        await wait_until(lambda: len(tool_calls(rig.events())) == 1)
        token.cancel()

    canceller = asyncio.create_task(cancel_after_first_item())
    outcome = await rig.agent.run("hi", cancel=token)
    await canceller
    events = rig.events()
    assert outcome.status == "interrupted"
    assert tool_calls(events) == [first]
    assert not any(isinstance(item, Message) for item in items(events))
    assert bodies(events, ModelRequestFinished)[0].stop_reason == "aborted"
    assert bodies(events, ToolCallStarted) == []
    assert results(events) == [
        ToolResult(call_id=first.id, status="interrupted", content=NOT_EXECUTED)
    ]
    assert bodies(events, Interrupted) == [Interrupted(phase="streaming")]
    assert kinds(events)[-4:] == [
        "model_request_finished",
        "item",
        "interrupted",
        "session_finished",
    ]
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


@pytest.mark.parametrize(
    ("side_effect", "status"),
    [("read_only", "interrupted"), ("mutating", "unknown"), ("external", "unknown")],
)
async def test_cancel_during_a_tool_kills_it_and_classifies_the_result(
    rigs: RigFactory, side_effect: SideEffect, status: str
) -> None:
    started = asyncio.Event()
    waiting = call("wait_cancel")
    rig = rigs.make([FakeTurn(items=(waiting,))], [wait_cancel_tool(side_effect, started)])
    token = CancelToken()
    canceller = asyncio.create_task(_cancel_when(started, token))
    outcome = await rig.agent.run("hi", cancel=token)
    await canceller
    events = rig.events()
    assert outcome.status == "interrupted"
    assert kinds(events)[-5:] == [
        "tool_call_started",
        "tool_call_finished",
        "item",
        "interrupted",
        "session_finished",
    ]
    [finished] = bodies(events, ToolCallFinished)
    assert finished.exit_status == "killed"
    [result] = results(events)
    assert result.call_id == waiting.id
    assert result.status == status
    assert result.content.startswith("partial output\n")
    assert "killed by an interrupt" in result.content
    assert bodies(events, Interrupted) == [Interrupted(phase="tool", killed=(waiting.id,))]
    assert len(rig.provider.requests) == 1
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


async def test_mixed_batch_kills_the_running_call_and_skips_the_rest(rigs: RigFactory) -> None:
    started = asyncio.Event()
    waiting = call("wait_cancel")
    append = call("append", path="out.txt", line="x")
    rig = rigs.make(
        [FakeTurn(items=(waiting, append))],
        [wait_cancel_tool("read_only", started), append_tool()],
    )
    token = CancelToken()
    canceller = asyncio.create_task(_cancel_when(started, token))
    outcome = await rig.agent.run("hi", cancel=token)
    await canceller
    events = rig.events()
    assert outcome.status == "interrupted"
    assert [b.call_id for b in bodies(events, ToolCallStarted)] == [waiting.id]
    by_id = {r.call_id: r for r in results(events)}
    assert by_id[waiting.id].status == "interrupted"
    assert "killed by an interrupt" in by_id[waiting.id].content
    assert by_id[append.id] == ToolResult(
        call_id=append.id, status="interrupted", content=NOT_EXECUTED
    )
    assert not (rig.workdir / "out.txt").exists()
    assert bodies(events, Interrupted) == [Interrupted(phase="tool", killed=(waiting.id,))]
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


async def test_concurrent_batch_is_closed_out_before_the_run_ends(rigs: RigFactory) -> None:
    first_started, second_started = asyncio.Event(), asyncio.Event()
    first = call("wait_a")
    second = call("wait_b")
    rig = rigs.make(
        [FakeTurn(items=(first, second))],
        [
            wait_cancel_tool("read_only", first_started, name="wait_a"),
            wait_cancel_tool("read_only", second_started, name="wait_b"),
        ],
    )
    token = CancelToken()

    async def cancel_when_both_started() -> None:
        await first_started.wait()
        await second_started.wait()
        token.cancel()

    canceller = asyncio.create_task(cancel_when_both_started())
    outcome = await rig.agent.run("hi", cancel=token)
    await canceller
    events = rig.events()
    assert outcome.status == "interrupted"
    assert [b.exit_status for b in bodies(events, ToolCallFinished)] == ["killed", "killed"]
    assert {r.call_id: r.status for r in results(events)} == {
        first.id: "interrupted",
        second.id: "interrupted",
    }
    assert bodies(events, Interrupted) == [
        Interrupted(phase="tool", killed=(first.id, second.id))
    ]
    assert kinds(events)[-2:] == ["interrupted", "session_finished"]
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


async def test_only_the_internal_token_reaches_tools(rigs: RigFactory) -> None:
    seen: list[CancelToken] = []

    async def capture(args: NoArgs, ctx: ToolContext) -> ToolOutput:
        seen.append(ctx.cancel)
        return ToolOutput(content="ok")

    tool = Tool(
        name="capture", description="", args_model=NoArgs, side_effect="read_only",
        handler=capture,
    )
    rig = rigs.make([FakeTurn(items=(call("capture"),)), FakeTurn(items=(say("ok"),))], [tool])
    token = CancelToken()
    outcome = await rig.agent.run("hi", cancel=token)
    assert outcome.status == "done"
    [internal] = seen
    assert internal is not token
    assert not token.cancelled
    assert_no_leaked_tasks()


async def test_wall_clock_ends_with_budget_exceeded(rigs: RigFactory) -> None:
    waiting = call("wait_cancel")
    rig = rigs.make(
        [FakeTurn(items=(waiting,))],
        [wait_cancel_tool("mutating")],
        limits=Limits(max_turns=50, max_wall_s=0.2),
    )
    outcome = await rig.agent.run("hi")
    events = rig.events()
    assert outcome.status == "budget_exceeded"
    assert bodies(events, Interrupted) == []
    [exceeded] = bodies(events, BudgetExceeded)
    assert exceeded.limit == "wall_clock"
    assert exceeded.value >= 0.2
    assert bodies(events, ToolCallFinished)[0].exit_status == "killed"
    assert results(events)[0].status == "unknown"
    assert kinds(events)[-4:] == [
        "tool_call_finished",
        "item",
        "budget_exceeded",
        "session_finished",
    ]
    assert bodies(events, SessionFinished)[0].status == "budget_exceeded"
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


async def test_token_budget_stops_before_the_next_request(rigs: RigFactory) -> None:
    echo = call("echo", text="x")
    rig = rigs.make(
        [
            FakeTurn(items=(echo,), usage=Usage(input=80, cache_read=20, output=10)),
            FakeTurn(items=(say("never"),)),
        ],
        [echo_tool()],
        limits=Limits(max_turns=50, max_tokens=50),
    )
    outcome = await rig.agent.run("hi")
    events = rig.events()
    assert outcome.status == "budget_exceeded"
    assert len(rig.provider.requests) == 1
    assert bodies(events, BudgetExceeded) == [BudgetExceeded(limit="tokens", value=110)]
    assert results(events)[0].status == "ok"
    assert kinds(events)[-3:] == ["item", "budget_exceeded", "session_finished"]
    assert outcome.usage == Usage(input=80, cache_read=20, output=10)
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


def test_cost_limit_is_rejected_at_construction(rigs: RigFactory) -> None:
    with pytest.raises(ValueError, match="cost limits need pricing, which arrives in M3"):
        rigs.make([], limits=Limits(max_cost_usd=1.0))


async def test_watchers_are_cleaned_up_when_run_raises(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("x"),))], limits=Limits(max_turns=5, max_wall_s=30))
    rig.log.close()
    with pytest.raises(ValueError, match="closed"):
        await rig.agent.run("hi", cancel=CancelToken())
    assert_no_leaked_tasks()


async def test_caller_cancel_after_the_run_has_no_effect(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("x"),)), FakeTurn(items=(say("y"),))])
    token = CancelToken()
    first = await rig.agent.run("hi", cancel=token)
    token.cancel()
    second = await rig.agent.run("again")
    assert (first.status, second.status) == ("done", "done")
    assert_no_leaked_tasks()
