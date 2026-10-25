from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

from agent_helpers import assert_every_call_resolved, tool_calls
from harness.agent.loop import Agent, AgentConfig
from harness.agent.repair import NOT_EXECUTED, RepairAction, plan_repairs
from harness.agent.tools import ToolContext
from harness.chaos.scenario import ScenarioProvider, chaos_tools, naive_plan_repairs
from harness.core.cancel import CancelToken
from harness.core.events import (
    Event,
    EventBody,
    ItemEvent,
    Limits,
    ModelRequestStarted,
    SideEffect,
    ToolCallStarted,
    make_event,
)
from harness.core.items import Item, Message, ToolCall, ToolResult, ToolResultStatus, text_message
from harness.env.local import LocalEnvironment
from harness.providers.base import ItemDone, ModelRequest, StreamEnd, StreamEvent, UsageReport
from harness.providers.fake import DEFAULT_CAPS
from harness.store.jsonl import JsonlSessionLog, JsonlSessionStore

TARGET = "out.txt"
LINES = ("L1", "L2", "L3")


class _Crash(BaseException):
    pass


@pytest.fixture
def store(tmp_path: Path) -> JsonlSessionStore:
    return JsonlSessionStore(tmp_path / "sessions")


@pytest.fixture
def log(store: JsonlSessionStore) -> Iterator[JsonlSessionLog]:
    opened = store.open("s1", create=True)
    yield opened
    opened.close()


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


Planner = Callable[[Sequence[Event]], list[RepairAction]]


def _agent(
    log: JsonlSessionLog,
    workdir: Path,
    *,
    after_effect: Callable[[str], None] | None = None,
    repair_planner: Planner = plan_repairs,
) -> tuple[Agent, ScenarioProvider]:
    provider = ScenarioProvider(LINES, per_step=2)
    agent = Agent(
        log=log,
        provider=provider,
        env=LocalEnvironment(workdir),
        tools=chaos_tools(TARGET, after_effect),
        config=AgentConfig(
            model="scenario-1", system_prompt="append the lines", limits=Limits(max_turns=20)
        ),
        repair_planner=repair_planner,
    )
    return agent, provider


def _file_lines(workdir: Path) -> list[str]:
    return (workdir / TARGET).read_text().splitlines()


def _calls_per_request(events: Sequence[Event]) -> list[int]:
    request_ids = [e.body.request_id for e in events if isinstance(e.body, ModelRequestStarted)]
    per_request = dict.fromkeys(request_ids, 0)
    for event in events:
        body = event.body
        if isinstance(body, ItemEvent) and isinstance(body.item, ToolCall) and body.request_id:
            per_request[body.request_id] += 1
    return list(per_request.values())


def _append(call_id: str, line: str) -> ToolCall:
    return ToolCall(
        id=call_id, provider_call_id=f"p_{call_id}", name="append_line", arguments={"line": line}
    )


def _read(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, provider_call_id=f"p_{call_id}", name="read_target", arguments={})


def _result(call_id: str, status: ToolResultStatus, content: str = "") -> ToolResult:
    return ToolResult(call_id=call_id, status=status, content=content)


def _request(*items: Item) -> ModelRequest:
    return ModelRequest(
        model="scenario-1",
        system="append the lines",
        items=(text_message("user", "go"), *items),
    )


def _decide(lines: Sequence[str], *items: Item) -> tuple[Item, ...]:
    return ScenarioProvider(lines, per_step=2).respond(_request(*items))


def _appended_lines(response: Sequence[Item]) -> list[str]:
    assert all(isinstance(i, ToolCall) and i.name == "append_line" for i in response)
    return [i.arguments["line"] for i in response if isinstance(i, ToolCall)]


def _is_read(response: Sequence[Item]) -> bool:
    return (
        len(response) == 1
        and isinstance(response[0], ToolCall)
        and response[0].name == "read_target"
    )


async def _collect(provider: ScenarioProvider, request: ModelRequest) -> list[StreamEvent]:
    return [event async for event in provider.stream(request, CancelToken())]


async def test_fresh_run_appends_each_line_once_in_three_requests(
    log: JsonlSessionLog, workdir: Path
) -> None:
    agent, provider = _agent(log, workdir)

    outcome = await agent.run("append the lines")

    events = log.read()
    assert outcome.status == "done"
    assert outcome.turns == 3
    assert len(provider.requests) == 3
    assert _calls_per_request(events) == [2, 1, 0]
    final = [e.body.item for e in events if isinstance(e.body, ItemEvent)][-1]
    assert isinstance(final, Message) and final.text == "done"
    assert _file_lines(workdir) == list(LINES)
    assert_every_call_resolved(events)


async def test_stream_shape_and_caps() -> None:
    provider = ScenarioProvider(LINES, per_step=2)

    events = await _collect(provider, _request())

    assert provider.name == "scenario"
    assert provider.capabilities("anything") == DEFAULT_CAPS
    assert [type(e) for e in events] == [ItemDone, ItemDone, UsageReport, StreamEnd]
    end = events[-1]
    assert isinstance(end, StreamEnd) and end.stop_reason == "tool_use"
    finished = _request(
        _append("a", "L1"),
        _append("b", "L2"),
        _result("a", "ok"),
        _result("b", "ok"),
        _append("c", "L3"),
        _result("c", "ok"),
    )
    last = await _collect(provider, finished)
    assert [type(e) for e in last] == [ItemDone, UsageReport, StreamEnd]
    last_end = last[-1]
    assert isinstance(last_end, StreamEnd) and last_end.stop_reason == "end_turn"
    usage = [e.usage for e in (*events, *last) if isinstance(e, UsageReport)]
    assert usage[0] == usage[1]


def test_unknown_result_leads_to_read() -> None:
    response = _decide(LINES, _append("a", "L1"), _result("a", "unknown", "may have run"))

    assert _is_read(response)


def test_read_containing_line_confirms_it() -> None:
    history = (
        _append("a", "L1"),
        _result("a", "unknown"),
        _read("r"),
        _result("r", "ok", "L1\n"),
    )

    assert _appended_lines(_decide(LINES, *history)) == ["L2", "L3"]
    done = _decide(["L1"], *history)
    assert len(done) == 1 and isinstance(done[0], Message) and done[0].text == "done"


@pytest.mark.parametrize("content", ["", "L10\n"])
def test_read_lacking_line_leads_to_reappend(content: str) -> None:
    response = _decide(
        ["L1"], _append("a", "L1"), _result("a", "unknown"), _read("r"), _result("r", "ok", content)
    )

    assert _appended_lines(response) == ["L1"]


def test_interrupted_result_leads_to_reappend_without_read() -> None:
    response = _decide(LINES, _append("a", "L1"), _result("a", "interrupted", NOT_EXECUTED))

    assert _appended_lines(response) == ["L1", "L2"]


def test_read_that_did_not_complete_keeps_the_line_suspect() -> None:
    response = _decide(
        LINES,
        _append("a", "L1"),
        _result("a", "unknown"),
        _read("r"),
        _result("r", "interrupted", NOT_EXECUTED),
    )

    assert _is_read(response)


def test_new_call_ids_never_collide_with_ids_in_the_request() -> None:
    first = _decide(LINES)
    first_ids = [i.id for i in first if isinstance(i, ToolCall)]
    results = [_result(call_id, "interrupted") for call_id in first_ids]

    second = _decide(LINES, *first, *results)

    second_ids = [i.id for i in second if isinstance(i, ToolCall)]
    assert len(first_ids) == 2 and len(second_ids) == 2
    assert not set(first_ids) & set(second_ids)
    assert first_ids == [i.id for i in _decide(LINES) if isinstance(i, ToolCall)]


def _events(*bodies: EventBody) -> list[Event]:
    return [make_event(seq, body) for seq, body in enumerate(bodies)]


def _started(call_id: str, tool: str, side_effect: SideEffect) -> ToolCallStarted:
    return ToolCallStarted(call_id=call_id, tool=tool, arguments={}, side_effect=side_effect)


def test_naive_repairs_call_every_dangling_call_not_executed() -> None:
    events = _events(
        ItemEvent(item=_append("m", "L1")),
        ItemEvent(item=_read("r")),
        ItemEvent(item=_append("n", "L2")),
        _started("m", "append_line", "mutating"),
        _started("r", "read_target", "read_only"),
    )

    naive = naive_plan_repairs(events)

    assert naive == [
        RepairAction(call_id=c, tool=t, status="interrupted", reason=NOT_EXECUTED)
        for c, t in (("m", "append_line"), ("r", "read_target"), ("n", "append_line"))
    ]
    assert plan_repairs(events)[0].status == "unknown"


async def test_stream_honors_cancel_and_keeps_finished_items() -> None:
    provider = ScenarioProvider(LINES, per_step=2)
    token = CancelToken()
    token.cancel()

    early = [e async for e in provider.stream(_request(), token)]

    assert early == [StreamEnd(stop_reason="aborted")]
    token = CancelToken()
    seen: list[StreamEvent] = []
    async for event in provider.stream(_request(), token):
        seen.append(event)
        if isinstance(event, ItemDone):
            token.cancel()
    assert len(seen) == 2
    assert isinstance(seen[0], ItemDone)
    assert seen[1] == StreamEnd(stop_reason="aborted")


async def test_read_target_returns_empty_for_missing_file(workdir: Path) -> None:
    tool = chaos_tools(TARGET).get("read_target")
    assert tool is not None
    ctx = ToolContext(
        env=LocalEnvironment(workdir),
        cancel=CancelToken(),
        cwd=".",
        timeout_s=10.0,
        max_output_bytes=10_000,
    )

    missing = await tool.handler(tool.args_model(), ctx)
    (workdir / TARGET).write_text("L1\n")
    present = await tool.handler(tool.args_model(), ctx)

    assert missing.status == "ok" and missing.content == ""
    assert present.content == "L1\n"
    append = chaos_tools(TARGET).get("append_line")
    assert append is not None and append.side_effect == "mutating"
    assert tool.side_effect == "read_only"


@pytest.mark.parametrize(
    ("planner", "expected"),
    [(plan_repairs, ["L1", "L2", "L3"]), (naive_plan_repairs, ["L1", "L1", "L2", "L3"])],
)
async def test_scenario_resumes_after_a_crash_between_effect_and_result(
    log: JsonlSessionLog, workdir: Path, planner: Planner, expected: list[str]
) -> None:
    def crash(line: str) -> None:
        raise _Crash(line)

    crashing, _ = _agent(log, workdir, after_effect=crash)
    with pytest.raises(BaseExceptionGroup) as raised:
        await crashing.run("append the lines")
    assert raised.value.subgroup(_Crash) is not None

    resumed, _ = _agent(log, workdir, repair_planner=planner)
    outcome = await resumed.run()

    events = log.read()
    assert outcome.status == "done"
    assert sorted(_file_lines(workdir)) == expected
    assert_every_call_resolved(events)
    ids = [c.id for c in tool_calls(events)]
    assert len(ids) == len(set(ids))
