import asyncio
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from agent_helpers import (
    RigFactory,
    assert_every_call_resolved,
    assert_no_leaked_tasks,
    bodies,
    echo_tool,
    kinds,
    results,
    wait_cancel_tool,
)
from cookcode.agent.repair import (
    MUTATING_LOST,
    NOT_EXECUTED,
    READ_ONLY_LOST,
    RepairAction,
    unresolved_calls,
)
from cookcode.core.cancel import CancelToken
from cookcode.core.events import (
    Event,
    EventBody,
    ItemEvent,
    Limits,
    ModelRequestFinished,
    ModelRequestStarted,
    Repaired,
    RunConfig,
    SessionStarted,
    ToolCallFinished,
    ToolCallStarted,
    UserMessage,
)
from cookcode.core.items import ToolCall, ToolResult
from cookcode.core.usage import Usage
from cookcode.providers.fake import FakeTurn, call, say
from cookcode.store.jsonl import JsonlSessionLog


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def _started(call_id: str, side_effect: str) -> ToolCallStarted:
    return ToolCallStarted.model_validate(
        {"call_id": call_id, "tool": "echo", "arguments": {"text": "x"}, "side_effect": side_effect}
    )


def _finished(call_id: str) -> ToolCallFinished:
    return ToolCallFinished(
        call_id=call_id, exit_status="ok", duration_s=0.1, output_bytes=1, truncated=False
    )


LOST_SUFFIX = " It finished with exit status ok, but its result was lost."

STATES: dict[str, tuple[list[str], str, str]] = {
    "never_started": ([], "interrupted", NOT_EXECUTED),
    "started_read_only": (["read_only"], "interrupted", READ_ONLY_LOST),
    "started_mutating": (["mutating"], "unknown", MUTATING_LOST),
    "started_external": (["external"], "unknown", MUTATING_LOST),
    "finished_read_only": (["read_only", "finished"], "interrupted", READ_ONLY_LOST + LOST_SUFFIX),
    "finished_mutating": (["mutating", "finished"], "unknown", MUTATING_LOST + LOST_SUFFIX),
}


def _dangling_log(rigs: RigFactory, dangling: ToolCall, steps: Sequence[str]) -> JsonlSessionLog:
    extra: list[EventBody] = []
    for step in steps:
        if step == "finished":
            extra.append(_finished(dangling.id))
        else:
            extra.append(_started(dangling.id, step))
    log = rigs.open_log()
    for body in [
        SessionStarted.for_config(
            RunConfig(provider="fake", model="fake-1", harness_version="0.0.1")
        ),
        UserMessage(text="go"),
        ModelRequestStarted(request_id="r1", view="raw", token_estimate=1),
        ItemEvent(item=dangling, request_id="r1"),
        ModelRequestFinished(request_id="r1", stop_reason="tool_use", usage=Usage()),
        *extra,
    ]:
        log.append(body)
    log.close()
    return rigs.open_log()


@pytest.mark.parametrize("state", list(STATES))
async def test_resume_repairs_each_dangling_state(rigs: RigFactory, state: str) -> None:
    steps, status, reason = STATES[state]
    dangling = call("echo", text="x")
    log = _dangling_log(rigs, dangling, steps)
    prefix = len(log.read())
    rig = rigs.make([FakeTurn(items=(say("ok"),))], [echo_tool()], log=log)
    outcome = await rig.agent.run(None)
    new = rig.events()[prefix:]
    assert kinds(new) == [
        "run_started",
        "repaired",
        "item",
        "model_request_started",
        "item",
        "model_request_finished",
        "session_finished",
    ]
    assert new[1].body == Repaired(call_id=dangling.id, status=status, reason=reason)  # type: ignore[arg-type]
    assert results(new) == [ToolResult(call_id=dangling.id, status=status, content=reason)]  # type: ignore[arg-type]
    assert results(new)[0] in rig.provider.requests[0].items
    assert outcome.status == "done"
    assert_every_call_resolved(rig.events())
    assert_no_leaked_tasks()


async def test_repair_comes_before_the_new_prompt(rigs: RigFactory) -> None:
    dangling = call("echo", text="x")
    log = _dangling_log(rigs, dangling, ["mutating"])
    prefix = len(log.read())
    rig = rigs.make([FakeTurn(items=(say("ok"),))], [echo_tool()], log=log)
    await rig.agent.run("continue")
    assert kinds(rig.events()[prefix:])[:5] == [
        "run_started",
        "repaired",
        "item",
        "user_message",
        "model_request_started",
    ]
    assert kinds(rig.events()).count("session_started") == 1


async def test_repair_is_idempotent(rigs: RigFactory) -> None:
    dangling = call("echo", text="x")
    log = _dangling_log(rigs, dangling, ["read_only"])
    rig = rigs.make(
        [FakeTurn(items=(say("ok"),)), FakeTurn(items=(say("again"),))], [echo_tool()], log=log
    )
    await rig.agent.run(None)
    after_first = len(bodies(rig.events(), Repaired))
    await rig.agent.run("more")
    assert after_first == 1
    assert len(bodies(rig.events(), Repaired)) == 1
    assert_every_call_resolved(rig.events())


async def test_hard_cancel_leaves_the_log_for_repair(rigs: RigFactory) -> None:
    started = asyncio.Event()
    waiting = call("wait_cancel")
    rig = rigs.make(
        [FakeTurn(items=(waiting,))],
        [wait_cancel_tool("mutating", started)],
        limits=Limits(max_turns=50, max_wall_s=30),
    )
    task = asyncio.create_task(rig.agent.run("hi", cancel=CancelToken()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_no_leaked_tasks()
    events = rig.events()
    assert kinds(events)[-1] == "tool_call_started"
    assert bodies(events, ToolCallStarted)[-1].call_id == waiting.id
    assert "session_finished" not in kinds(events)

    rig.log.close()
    resumed = rigs.make(
        [FakeTurn(items=(say("ok"),))], [wait_cancel_tool("mutating")], log=rigs.open_log()
    )
    outcome = await resumed.agent.run(None)
    after = resumed.events()
    assert bodies(after, Repaired) == [
        Repaired(call_id=waiting.id, status="unknown", reason=MUTATING_LOST)
    ]
    assert results(after) == [
        ToolResult(call_id=waiting.id, status="unknown", content=MUTATING_LOST)
    ]
    assert outcome.status == "done"
    assert_every_call_resolved(after)


async def test_custom_planner_replaces_repair(rigs: RigFactory) -> None:
    dangling = call("echo", text="x")
    log = _dangling_log(rigs, dangling, ["mutating"])
    seen: list[int] = []

    def no_repairs(events: Sequence[Event]) -> list[RepairAction]:
        seen.append(len(events))
        return []

    rig = rigs.make(
        [FakeTurn(items=(say("ok"),))], [echo_tool()], log=log, repair_planner=no_repairs
    )
    outcome = await rig.agent.run(None)
    events = rig.events()
    assert seen == [6]
    assert bodies(events, Repaired) == []
    assert unresolved_calls(events) == [dangling]
    assert outcome.status == "done"
