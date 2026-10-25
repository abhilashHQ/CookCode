import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

import harness
from agent_helpers import (
    Rig,
    RigFactory,
    Timeline,
    append_tool,
    assert_every_call_resolved,
    bodies,
    boom_tool,
    echo_tool,
    gate_tool,
    index_of,
    is_result_for,
    kinds,
    results,
    steer_tool,
    tool_calls,
    wait_until,
)
from harness.agent.loop import Agent, AgentConfig
from harness.agent.repair import NOT_EXECUTED
from harness.agent.tools import ToolRegistry
from harness.core.cancel import CancelToken
from harness.core.events import (
    BudgetExceeded,
    ErrorEvent,
    Limits,
    ModelRequestFinished,
    SessionFinished,
    SessionStarted,
    SteerMessage,
    ToolCallFinished,
    ToolCallStarted,
    UserMessage,
)
from harness.core.items import ToolCall, ToolResult, text_message
from harness.core.usage import Usage
from harness.env.local import LocalEnvironment
from harness.providers.base import ItemDone, ModelCaps, ModelRequest, StreamEvent
from harness.providers.fake import FakeTurn, call, say


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


async def test_event_order_for_one_tool_call(rigs: RigFactory) -> None:
    echo = call("echo", text="x")
    rig = rigs.make(
        [FakeTurn(items=(echo,)), FakeTurn(items=(say("done"),))], [echo_tool()]
    )
    outcome = await rig.agent.run("hi")
    events = rig.events()
    assert kinds(events) == [
        "session_started",
        "user_message",
        "model_request_started",
        "item",
        "model_request_finished",
        "tool_call_started",
        "tool_call_finished",
        "item",
        "model_request_started",
        "item",
        "model_request_finished",
        "session_finished",
    ]
    assert tool_calls(events) == [echo]
    finishes = bodies(events, ModelRequestFinished)
    assert [f.stop_reason for f in finishes] == ["tool_use", "end_turn"]
    assert results(events) == [ToolResult(call_id=echo.id, status="ok", content="x")]
    assert bodies(events, SessionFinished) == [
        SessionFinished(status="done", turns=2, usage=Usage())
    ]
    assert outcome.status == "done"
    assert outcome.turns == 2
    assert outcome.last_seq == events[-1].seq
    assert_every_call_resolved(events)


async def test_session_started_records_the_run_config(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("ok"),))], [echo_tool(), boom_tool()])
    await rig.agent.run("hi")
    [started] = bodies(rig.events(), SessionStarted)
    config = started.config
    assert config.provider == "fake"
    assert config.model == "fake-1"
    assert config.tools == ("echo", "boom")
    assert config.system_prompt_sha256 == hashlib.sha256(b"be brief").hexdigest()
    assert config.harness_version == harness.__version__
    assert config.limits == Limits(max_turns=50)


async def test_second_run_continues_the_same_log(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("one"),)), FakeTurn(items=(say("two"),))])
    await rig.agent.run("hi")
    outcome = await rig.agent.run("again")
    events = rig.events()
    assert kinds(events).count("session_started") == 1
    assert kinds(events).count("session_finished") == 2
    assert kinds(events)[-1] == "session_finished"
    assert bodies(events, UserMessage) == [UserMessage(text="hi"), UserMessage(text="again")]
    assert outcome.turns == 1
    assert rig.provider.requests[1].items == (
        text_message("user", "hi"),
        say("one"),
        text_message("user", "again"),
    )


async def test_the_model_sees_its_results(rigs: RigFactory) -> None:
    echo = call("echo", text="x")
    rig = rigs.make([FakeTurn(items=(echo,)), FakeTurn(items=(say("done"),))], [echo_tool()])
    await rig.agent.run("hi")
    second = rig.provider.requests[1]
    assert second.items == (
        text_message("user", "hi"),
        echo,
        ToolResult(call_id=echo.id, status="ok", content="x"),
    )
    assert [schema.name for schema in second.tools] == ["echo"]
    assert second.system == "be brief"


@pytest.mark.parametrize(("max_parallel", "expected"), [(8, "ok"), (1, "error")])
async def test_read_only_calls_run_concurrently_up_to_the_cap(
    rigs: RigFactory, max_parallel: int, expected: str
) -> None:
    barrier = asyncio.Barrier(2)
    rig = rigs.make(
        [FakeTurn(items=(call("gate", label="a"), call("gate", label="b"))),
         FakeTurn(items=(say("done"),))],
        [gate_tool(barrier, timeout_s=0.2)],
        max_parallel_tools=max_parallel,
    )
    await rig.agent.run("hi")
    events = rig.events()
    assert [r.status for r in results(events)] == [expected, expected]
    assert_every_call_resolved(events)


async def test_mutating_calls_run_alone_and_in_order(rigs: RigFactory) -> None:
    timeline = Timeline()
    rig = rigs.make(
        [
            FakeTurn(items=(
                call("echo", text="r1", delay_s=0.05),
                call("append", path="out.txt", line="x"),
                call("echo", text="r2"),
            )),
            FakeTurn(items=(say("done"),)),
        ],
        [echo_tool(timeline), append_tool(timeline)],
    )
    await rig.agent.run("hi")
    assert timeline.at("end", "r1") < timeline.at("start", "append:x")
    assert timeline.at("end", "append:x") < timeline.at("start", "r2")
    assert (rig.workdir / "out.txt").read_text() == "x\n"
    assert_every_call_resolved(rig.events())


async def test_read_only_batch_overlaps(rigs: RigFactory) -> None:
    timeline = Timeline()
    rig = rigs.make(
        [
            FakeTurn(items=(
                call("echo", text="a", delay_s=0.05),
                call("echo", text="b", delay_s=0.05),
            )),
            FakeTurn(items=(say("done"),)),
        ],
        [echo_tool(timeline)],
    )
    await rig.agent.run("hi")
    assert timeline.at("start", "b") < timeline.at("end", "a")


async def test_results_are_logged_in_completion_order_and_sent_in_call_order(
    rigs: RigFactory,
) -> None:
    slow = call("echo", text="slow", delay_s=0.1)
    fast = call("echo", text="fast")
    rig = rigs.make(
        [FakeTurn(items=(slow, fast)), FakeTurn(items=(say("done"),))], [echo_tool()]
    )
    await rig.agent.run("hi")
    events = rig.events()
    assert [r.call_id for r in results(events)] == [fast.id, slow.id]
    sent = [i.call_id for i in rig.provider.requests[1].items if isinstance(i, ToolResult)]
    assert sent == [slow.id, fast.id]
    assert_every_call_resolved(events)


async def test_unknown_tool_and_invalid_args_never_execute(rigs: RigFactory) -> None:
    unknown = call("nope", text="x")
    invalid = call("echo")
    valid = call("echo", text="fine")
    rig = rigs.make(
        [FakeTurn(items=(unknown, invalid, valid)), FakeTurn(items=(say("done"),))],
        [echo_tool()],
    )
    outcome = await rig.agent.run("hi")
    events = rig.events()
    started = [b.call_id for b in bodies(events, ToolCallStarted)]
    finished = [b.call_id for b in bodies(events, ToolCallFinished)]
    assert started == [valid.id]
    assert finished == [valid.id]
    by_id = {r.call_id: r for r in results(events)}
    assert by_id[unknown.id].status == "error"
    assert "unknown tool" in by_id[unknown.id].content
    assert "nope" in by_id[unknown.id].content
    assert by_id[invalid.id].status == "error"
    assert "invalid arguments" in by_id[invalid.id].content
    assert "text" in by_id[invalid.id].content
    assert by_id[valid.id] == ToolResult(call_id=valid.id, status="ok", content="fine")
    assert outcome.status == "done"
    assert_every_call_resolved(events)


async def test_handler_exception_becomes_an_error_result(rigs: RigFactory) -> None:
    boom = call("boom")
    rig = rigs.make([FakeTurn(items=(boom,)), FakeTurn(items=(say("ok"),))], [boom_tool()])
    outcome = await rig.agent.run("hi")
    events = rig.events()
    [finished] = bodies(events, ToolCallFinished)
    assert finished.exit_status == "error"
    [result] = results(events)
    assert result.status == "error"
    assert "tool raised RuntimeError: boom" in result.content
    assert len(rig.provider.requests) == 2
    assert outcome.status == "done"
    assert_every_call_resolved(events)


async def test_steer_before_run_lands_before_the_first_request(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("ok"),))])
    rig.agent.steer("use tabs")
    await rig.agent.run("hi")
    events = rig.events()
    assert kinds(events)[:4] == [
        "session_started",
        "user_message",
        "steer_message",
        "model_request_started",
    ]
    assert bodies(events, SteerMessage) == [SteerMessage(text="use tabs")]
    assert rig.provider.requests[0].items[-1] == text_message("user", "use tabs")


async def test_steer_from_a_tool_lands_between_steps(rigs: RigFactory) -> None:
    holder: list[Rig] = []
    steer = call("steer", text="also lint")
    late = call("echo", text="late", delay_s=0.05)
    rig = rigs.make(
        [FakeTurn(items=(steer, late)), FakeTurn(items=(say("done"),))],
        [steer_tool(lambda text: holder[0].agent.steer(text)), echo_tool()],
    )
    holder.append(rig)
    await rig.agent.run("hi")
    events = rig.events()
    steer_at = index_of(events, lambda e: isinstance(e.body, SteerMessage))
    assert steer_at > index_of(events, is_result_for(late.id))
    assert steer_at > index_of(events, is_result_for(steer.id))
    assert kinds(events)[steer_at + 1] == "model_request_started"
    assert kinds(events).count("model_request_started") == 2


async def test_steer_during_final_answer_forces_another_request(rigs: RigFactory) -> None:
    rig = rigs.make(
        [
            FakeTurn(items=(call("echo", text="x"),)),
            FakeTurn(items=(say("all done, wrapping up the answer now"),)),
            FakeTurn(items=(say("ack"),)),
        ],
        [echo_tool()],
        event_delay_s=0.01,
    )

    async def steer_once_streaming() -> None:
        await wait_until(lambda: len(rig.provider.requests) == 2)
        rig.agent.steer("one more thing")

    steerer = asyncio.create_task(steer_once_streaming())
    outcome = await rig.agent.run("hi")
    await steerer
    events = rig.events()
    assert outcome.status == "done"
    assert outcome.turns == 3
    finishes = [i for i, e in enumerate(events) if isinstance(e.body, ModelRequestFinished)]
    steer_at = index_of(events, lambda e: isinstance(e.body, SteerMessage))
    assert finishes[1] < steer_at < finishes[2]
    assert rig.provider.requests[2].items[-1] == text_message("user", "one more thing")


async def test_turn_cap_ends_with_budget_exceeded(rigs: RigFactory) -> None:
    first = call("echo", text="1")
    rig = rigs.make(
        [FakeTurn(items=(first,)), FakeTurn(items=(call("echo", text="2"),))],
        [echo_tool()],
        limits=Limits(max_turns=1),
    )
    outcome = await rig.agent.run("hi")
    events = rig.events()
    assert outcome.status == "budget_exceeded"
    assert outcome.turns == 1
    assert len(rig.provider.requests) == 1
    assert bodies(events, BudgetExceeded) == [BudgetExceeded(limit="turns", value=1)]
    budget_at = index_of(events, lambda e: isinstance(e.body, BudgetExceeded))
    assert index_of(events, is_result_for(first.id)) < budget_at
    assert kinds(events)[budget_at:] == ["budget_exceeded", "session_finished"]
    assert bodies(events, SessionFinished)[0].status == "budget_exceeded"
    assert_every_call_resolved(events)


async def test_provider_error_ends_the_run(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(), fail_with="overloaded")])
    outcome = await rig.agent.run("hi")
    events = rig.events()
    assert outcome.status == "error"
    assert bodies(events, ModelRequestFinished)[0].stop_reason == "error"
    assert bodies(events, ErrorEvent) == [
        ErrorEvent(kind="provider", retryable=False, message="overloaded")
    ]
    assert kinds(events)[-2:] == ["error", "session_finished"]
    assert bodies(events, SessionFinished)[0].status == "error"


async def test_script_exhaustion_ends_the_run_as_error(rigs: RigFactory) -> None:
    rig = rigs.make([])
    outcome = await rig.agent.run("hi")
    events = rig.events()
    assert outcome.status == "error"
    assert outcome.turns == 1
    [error] = bodies(events, ErrorEvent)
    assert error.kind == "provider"
    assert "FakeScriptExhausted" in error.message
    assert bodies(events, ModelRequestFinished)[0].stop_reason == "error"
    assert bodies(events, SessionFinished)[0].status == "error"


class _RaisesAfterACall:
    name = "raising"

    def __init__(self, tool_call: ToolCall) -> None:
        self.tool_call = tool_call

    def capabilities(self, model: str) -> ModelCaps:
        raise NotImplementedError

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncIterator[StreamEvent]:
        yield ItemDone(index=0, item=self.tool_call)
        raise ConnectionError("socket closed")


async def test_stream_raising_after_a_call_closes_the_call_out(
    rigs: RigFactory, tmp_path: Path
) -> None:
    echo = call("echo", text="x")
    log = rigs.open_log()
    agent = Agent(
        log=log,
        provider=_RaisesAfterACall(echo),
        env=LocalEnvironment(tmp_path),
        tools=ToolRegistry([echo_tool()]),
        config=AgentConfig(model="fake-1", system_prompt="be brief"),
    )
    outcome = await agent.run("hi")
    events = log.read()
    assert outcome.status == "error"
    assert bodies(events, ToolCallStarted) == []
    assert results(events) == [
        ToolResult(call_id=echo.id, status="interrupted", content=NOT_EXECUTED)
    ]
    assert kinds(events)[-5:] == [
        "item",
        "model_request_finished",
        "item",
        "error",
        "session_finished",
    ]
    assert "ConnectionError: socket closed" in bodies(events, ErrorEvent)[0].message
    assert_every_call_resolved(events)


async def test_run_while_running_raises(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("a long enough answer"),))], event_delay_s=0.01)
    first = asyncio.create_task(rig.agent.run("hi"))
    await wait_until(lambda: len(rig.provider.requests) == 1)
    with pytest.raises(RuntimeError):
        await rig.agent.run("again")
    outcome = await first
    assert outcome.status == "done"
    assert kinds(rig.events()).count("user_message") == 1
