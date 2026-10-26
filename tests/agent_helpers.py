import asyncio
import shlex
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from harness.agent.approval import Approver, PolicyOnlyApprover
from harness.agent.loop import Agent, AgentConfig
from harness.agent.repair import RepairAction, plan_repairs, unresolved_calls
from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.events import Event, ItemEvent, Limits, SideEffect
from harness.core.items import Item, ToolCall, ToolResult
from harness.env.local import LocalEnvironment
from harness.providers.fake import FakeProvider, FakeTurn
from harness.store.base import CheckpointStore
from harness.store.jsonl import JsonlSessionLog, JsonlSessionStore

SESSION = "s1"


def kinds(events: Sequence[Event]) -> list[str]:
    return [event.body.type for event in events]


def items(events: Sequence[Event]) -> list[Item]:
    return [event.body.item for event in events if isinstance(event.body, ItemEvent)]


def results(events: Sequence[Event]) -> list[ToolResult]:
    return [item for item in items(events) if isinstance(item, ToolResult)]


def tool_calls(events: Sequence[Event]) -> list[ToolCall]:
    return [item for item in items(events) if isinstance(item, ToolCall)]


def bodies[B](events: Sequence[Event], kind: type[B]) -> list[B]:
    return [event.body for event in events if isinstance(event.body, kind)]


def index_of(events: Sequence[Event], predicate: Callable[[Event], bool]) -> int:
    return next(i for i, event in enumerate(events) if predicate(event))


def is_result_for(call_id: str) -> Callable[[Event], bool]:
    def check(event: Event) -> bool:
        body = event.body
        return (
            isinstance(body, ItemEvent)
            and isinstance(body.item, ToolResult)
            and body.item.call_id == call_id
        )

    return check


def assert_every_call_resolved(events: Sequence[Event]) -> None:
    assert unresolved_calls(events) == []
    counts = Counter(result.call_id for result in results(events))
    for call in tool_calls(events):
        assert counts[call.id] == 1, f"call {call.id} has {counts[call.id]} results"


def assert_no_leaked_tasks() -> None:
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def wait_until(predicate: Callable[[], bool], timeout_s: float = 5.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():
            await asyncio.sleep(0.001)


class Timeline:
    def __init__(self) -> None:
        self.marks: list[tuple[str, str]] = []

    def mark(self, what: Literal["start", "end"], label: str) -> None:
        self.marks.append((what, label))

    def at(self, what: Literal["start", "end"], label: str) -> int:
        return self.marks.index((what, label))


async def _race_cancel(
    work: Awaitable[Any], ctx: ToolContext, timeout_s: float | None
) -> Literal["done", "cancelled", "timeout"]:
    work_task = asyncio.ensure_future(work)
    stop_task = asyncio.ensure_future(ctx.cancel.wait())
    try:
        done, _ = await asyncio.wait(
            {work_task, stop_task}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        work_task.cancel()
        stop_task.cancel()
        await asyncio.gather(work_task, stop_task, return_exceptions=True)
    if work_task in done:
        return "done"
    if stop_task in done:
        return "cancelled"
    return "timeout"


def _killed(partial: str) -> ToolOutput:
    return ToolOutput(content=partial, status="error", exit_status="killed")


class EchoArgs(BaseModel):
    text: str
    delay_s: float = 0.0


def echo_tool(timeline: Timeline | None = None) -> Tool[EchoArgs]:
    async def handler(args: EchoArgs, ctx: ToolContext) -> ToolOutput:
        if timeline:
            timeline.mark("start", args.text)
        outcome = await _race_cancel(asyncio.sleep(args.delay_s), ctx, None)
        if timeline:
            timeline.mark("end", args.text)
        if outcome == "cancelled":
            return _killed(args.text)
        return ToolOutput(content=args.text)

    return Tool(
        name="echo",
        description="Return the text.",
        args_model=EchoArgs,
        side_effect="read_only",
        handler=handler,
    )


class AppendArgs(BaseModel):
    path: str
    line: str


def append_tool(timeline: Timeline | None = None) -> Tool[AppendArgs]:
    async def handler(args: AppendArgs, ctx: ToolContext) -> ToolOutput:
        label = f"append:{args.line}"
        if timeline:
            timeline.mark("start", label)
        result = await ctx.env.exec(
            f"printf '%s\\n' {shlex.quote(args.line)} >> {shlex.quote(args.path)}",
            cwd=ctx.cwd,
            timeout_s=ctx.timeout_s,
            max_output_bytes=ctx.max_output_bytes,
            cancel=ctx.cancel,
        )
        if timeline:
            timeline.mark("end", label)
        if result.cancelled:
            return _killed(result.output)
        if result.exit_code != 0:
            return ToolOutput(content=result.output, status="error", exit_status="error")
        return ToolOutput(content=f"appended to {args.path}")

    return Tool(
        name="append",
        description="Append a line to a file.",
        args_model=AppendArgs,
        side_effect="mutating",
        handler=handler,
    )


class GateArgs(BaseModel):
    label: str = ""


def gate_tool(barrier: asyncio.Barrier, timeout_s: float = 0.3) -> Tool[GateArgs]:
    async def handler(args: GateArgs, ctx: ToolContext) -> ToolOutput:
        outcome = await _race_cancel(barrier.wait(), ctx, timeout_s)
        if outcome == "done":
            return ToolOutput(content="peers arrived")
        if outcome == "cancelled":
            return _killed("")
        return ToolOutput(content="timed out waiting for peers", status="error")

    return Tool(
        name="gate",
        description="Wait for peers at a barrier.",
        args_model=GateArgs,
        side_effect="read_only",
        handler=handler,
    )


class NoArgs(BaseModel):
    pass


def wait_cancel_tool(
    side_effect: SideEffect, started: asyncio.Event | None = None, name: str = "wait_cancel"
) -> Tool[NoArgs]:
    async def handler(args: NoArgs, ctx: ToolContext) -> ToolOutput:
        if started:
            started.set()
        await ctx.cancel.wait()
        return _killed("partial output")

    return Tool(
        name=name,
        description="Block until cancelled.",
        args_model=NoArgs,
        side_effect=side_effect,
        handler=handler,
    )


def boom_tool() -> Tool[NoArgs]:
    async def handler(args: NoArgs, ctx: ToolContext) -> ToolOutput:
        raise RuntimeError("boom")

    return Tool(
        name="boom",
        description="Always raises.",
        args_model=NoArgs,
        side_effect="read_only",
        handler=handler,
    )


class SteerArgs(BaseModel):
    text: str


def steer_tool(steer: Callable[[str], None]) -> Tool[SteerArgs]:
    async def handler(args: SteerArgs, ctx: ToolContext) -> ToolOutput:
        steer(args.text)
        return ToolOutput(content="steered")

    return Tool(
        name="steer",
        description="Queue steering text.",
        args_model=SteerArgs,
        side_effect="read_only",
        handler=handler,
    )


@dataclass
class Rig:
    agent: Agent
    log: JsonlSessionLog
    provider: FakeProvider
    workdir: Path

    def events(self) -> list[Event]:
        return self.log.read()


@dataclass
class RigFactory:
    root: Path
    logs: list[JsonlSessionLog] = field(default_factory=list[JsonlSessionLog])

    @property
    def store(self) -> JsonlSessionStore:
        return JsonlSessionStore(self.root / "sessions")

    @property
    def workdir(self) -> Path:
        path = self.root / "work"
        path.mkdir(exist_ok=True)
        return path

    def open_log(self, session_id: str = SESSION) -> JsonlSessionLog:
        log = self.store.open(session_id, create=True)
        self.logs.append(log)
        return log

    def make(
        self,
        script: Iterable[FakeTurn],
        tools: Iterable[Tool[Any]] = (),
        *,
        log: JsonlSessionLog | None = None,
        limits: Limits | None = None,
        event_delay_s: float = 0.0,
        repair_planner: Callable[[Sequence[Event]], list[RepairAction]] = plan_repairs,
        approver: Approver | None = None,
        checkpoints: CheckpointStore | None = None,
        **config: Any,
    ) -> Rig:
        log = log or self.open_log()
        provider = FakeProvider(script, event_delay_s=event_delay_s)
        config.setdefault("mode", "yolo")
        agent_config = AgentConfig(
            model="fake-1",
            system_prompt="be brief",
            limits=limits or Limits(max_turns=50),
            **config,
        )
        agent = Agent(
            log=log,
            provider=provider,
            env=LocalEnvironment(self.workdir),
            tools=ToolRegistry(tools),
            config=agent_config,
            repair_planner=repair_planner,
            approver=approver or PolicyOnlyApprover(),
            checkpoints=checkpoints,
        )
        return Rig(agent=agent, log=log, provider=provider, workdir=self.workdir)

    def close_all(self) -> None:
        for log in self.logs:
            log.close()
