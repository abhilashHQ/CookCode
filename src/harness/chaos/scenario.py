import asyncio
import posixpath
import shlex
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Literal

from pydantic import BaseModel

from harness.agent.repair import NOT_EXECUTED, RepairAction, unresolved_calls
from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.cancel import CancelToken
from harness.core.events import Event
from harness.core.items import Item, ToolCall, ToolResult, ToolResultStatus, text_message
from harness.core.usage import Usage
from harness.providers.base import (
    ItemDone,
    ModelCaps,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    UsageReport,
)
from harness.providers.fake import DEFAULT_CAPS

APPEND_LINE = "append_line"
READ_TARGET = "read_target"
DONE_TEXT = "done"
SCENARIO_USAGE = Usage(input=100, output=20)


class AppendLineArgs(BaseModel):
    line: str


class ReadTargetArgs(BaseModel):
    pass


def _killed(partial: str) -> ToolOutput:
    return ToolOutput(content=partial, status="error", exit_status="killed")


def chaos_tools(target: str, after_effect: Callable[[str], None] | None = None) -> ToolRegistry:
    async def append_line(args: AppendLineArgs, ctx: ToolContext) -> ToolOutput:
        result = await ctx.env.exec(
            f"printf '%s\\n' {shlex.quote(args.line)} >> {shlex.quote(target)}",
            cwd=ctx.cwd,
            timeout_s=ctx.timeout_s,
            max_output_bytes=ctx.max_output_bytes,
            cancel=ctx.cancel,
        )
        if result.cancelled:
            return _killed(result.output)
        if result.timed_out:
            return ToolOutput(content=result.output, status="error", exit_status="timeout")
        if result.exit_code != 0:
            return ToolOutput(content=result.output, status="error", exit_status="error")
        if after_effect is not None:
            after_effect(args.line)
        return ToolOutput(content=f"appended {args.line!r} to {target}")

    async def read_target(args: ReadTargetArgs, ctx: ToolContext) -> ToolOutput:
        path = target if posixpath.isabs(target) else posixpath.join(ctx.cwd, target)
        try:
            data = await ctx.env.read_file(path)
        except FileNotFoundError:
            return ToolOutput(content="")
        return ToolOutput(content=data.decode(errors="replace"))

    return ToolRegistry(
        [
            Tool(
                name=APPEND_LINE,
                description=f"Append one line to {target}.",
                args_model=AppendLineArgs,
                side_effect="mutating",
                handler=append_line,
            ),
            Tool(
                name=READ_TARGET,
                description=f"Return the content of {target}, or an empty string if it is missing.",
                args_model=ReadTargetArgs,
                side_effect="read_only",
                handler=read_target,
            ),
        ]
    )


def naive_plan_repairs(events: Sequence[Event]) -> list[RepairAction]:
    return [
        RepairAction(call_id=call.id, tool=call.name, status="interrupted", reason=NOT_EXECUTED)
        for call in unresolved_calls(events)
    ]


LineState = Literal["confirmed", "suspect", "unconfirmed"]


def _classify(
    line: str,
    results: Sequence[tuple[int, ToolResultStatus]],
    reads: Sequence[tuple[int, frozenset[str]]],
) -> LineState:
    if any(status == "ok" for _, status in results):
        return "confirmed"
    unknown_at = [position for position, status in results if status == "unknown"]
    if not unknown_at:
        return "unconfirmed"
    later = [present for position, present in reads if position > unknown_at[-1]]
    if any(line in present for present in later):
        return "confirmed"
    if results[-1][1] == "unknown" and not later:
        return "suspect"
    return "unconfirmed"


class ScenarioProvider:
    name = "scenario"

    def __init__(self, lines: Sequence[str], per_step: int = 2) -> None:
        if per_step < 1:
            raise ValueError("per_step must be at least 1")
        if len(set(lines)) != len(lines):
            raise ValueError("lines must be unique")
        if any(not line or "\n" in line or "\r" in line for line in lines):
            raise ValueError("lines must be non-empty and single-line")
        self._lines = tuple(lines)
        self._per_step = per_step
        self.requests: list[ModelRequest] = []

    def capabilities(self, model: str) -> ModelCaps:
        return DEFAULT_CAPS

    def respond(self, request: ModelRequest) -> tuple[Item, ...]:
        histories: dict[str, list[tuple[int, ToolResultStatus]]] = {
            line: [] for line in self._lines
        }
        append_line_of: dict[str, str] = {}
        read_ids: set[str] = set()
        reads: list[tuple[int, frozenset[str]]] = []
        known_ids: set[str] = set()
        for position, item in enumerate(request.items):
            if isinstance(item, ToolCall):
                known_ids.add(item.id)
                line = item.arguments.get("line")
                if item.name == APPEND_LINE and isinstance(line, str) and line in histories:
                    append_line_of[item.id] = line
                elif item.name == READ_TARGET:
                    read_ids.add(item.id)
            elif isinstance(item, ToolResult):
                if item.call_id in append_line_of:
                    histories[append_line_of[item.call_id]].append((position, item.status))
                elif item.call_id in read_ids and item.status == "ok":
                    reads.append((position, frozenset(item.content.splitlines())))

        states = {line: _classify(line, histories[line], reads) for line in self._lines}
        ids = _fresh_ids(known_ids)
        if "suspect" in states.values():
            return (_call(next(ids), READ_TARGET, {}),)
        pending = [line for line in self._lines if states[line] == "unconfirmed"]
        if pending:
            return tuple(
                _call(next(ids), APPEND_LINE, {"line": line}) for line in pending[: self._per_step]
            )
        return (text_message("assistant", DONE_TEXT),)

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        if cancel.cancelled:
            yield StreamEnd(stop_reason="aborted")
            return
        response = self.respond(request)
        for index, item in enumerate(response):
            await asyncio.sleep(0)
            if cancel.cancelled:
                yield StreamEnd(stop_reason="aborted")
                return
            yield ItemDone(index=index, item=item)
        if cancel.cancelled:
            yield StreamEnd(stop_reason="aborted")
            return
        yield UsageReport(usage=SCENARIO_USAGE)
        has_calls = any(isinstance(item, ToolCall) for item in response)
        yield StreamEnd(stop_reason="tool_use" if has_calls else "end_turn")


def _fresh_ids(taken: set[str]) -> Iterator[str]:
    counter = len(taken)
    while True:
        candidate = f"scenario-{counter:04d}"
        counter += 1
        if candidate not in taken:
            yield candidate


def _call(call_id: str, name: str, arguments: dict[str, str]) -> ToolCall:
    return ToolCall(
        id=call_id, provider_call_id=f"call_{call_id}", name=name, arguments=dict(arguments)
    )
