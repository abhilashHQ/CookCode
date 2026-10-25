import asyncio
import hashlib
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from harness import __version__
from harness.agent.context import build_request, estimate_tokens
from harness.agent.repair import NOT_EXECUTED, RepairAction, plan_repairs, repair_bodies
from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.cancel import CancelToken
from harness.core.events import (
    BudgetExceeded,
    ErrorEvent,
    Event,
    ItemEvent,
    Limits,
    ModelRequestFinished,
    ModelRequestStarted,
    RunConfig,
    SessionFinished,
    SessionStarted,
    SteerMessage,
    ToolCallFinished,
    ToolCallStarted,
    UserMessage,
)
from harness.core.ids import new_id
from harness.core.items import ToolCall, ToolResult, ToolResultStatus
from harness.core.usage import StopReason, Usage
from harness.env.base import Environment
from harness.providers.base import ItemDone, ModelRequest, Provider, StreamEnd, UsageReport
from harness.store.base import SessionLog

RunStatus = Literal["done", "error", "budget_exceeded", "interrupted"]


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    system_prompt: str
    limits: Limits = Limits(max_turns=50)
    tool_timeout_s: float = 120.0
    max_tool_output_bytes: int = 30_000
    max_parallel_tools: int = 8
    cwd: str = "."


class RunOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RunStatus
    turns: int
    usage: Usage
    last_seq: int


@dataclass
class _RunState:
    token: CancelToken
    turns: int = 0
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class _Response:
    stop_reason: StopReason
    usage: Usage
    raw_usage: dict[str, Any]
    error: str | None
    calls: list[ToolCall]


@dataclass(frozen=True)
class _Dispatch:
    call: ToolCall
    tool: Tool[Any]
    args: BaseModel


def _batches(dispatches: Sequence[_Dispatch]) -> list[list[_Dispatch]]:
    batches: list[list[_Dispatch]] = []
    for dispatch in dispatches:
        read_only = dispatch.tool.side_effect == "read_only"
        if read_only and batches and batches[-1][0].tool.side_effect == "read_only":
            batches[-1].append(dispatch)
        else:
            batches.append([dispatch])
    return batches


def _describe_validation(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in detail['loc']) or '<root>'}: {detail['msg']}"
        for detail in error.errors(include_url=False)
    )


class Agent:
    def __init__(
        self,
        *,
        log: SessionLog,
        provider: Provider,
        env: Environment,
        tools: ToolRegistry,
        config: AgentConfig,
        harness_version: str = __version__,
        repair_planner: Callable[[Sequence[Event]], list[RepairAction]] = plan_repairs,
    ) -> None:
        self._log = log
        self._provider = provider
        self._env = env
        self._tools = tools
        self._config = config
        self._harness_version = harness_version
        self._repair_planner = repair_planner
        self._steering: deque[str] = deque()
        self._active = False

    def steer(self, text: str) -> None:
        self._steering.append(text)

    async def run(
        self, prompt: str | None = None, *, cancel: CancelToken | None = None
    ) -> RunOutcome:
        if self._active:
            raise RuntimeError("Agent.run() is already active")
        self._active = True
        try:
            return await self._execute(_RunState(token=CancelToken()), prompt)
        finally:
            self._active = False

    async def _execute(self, run: _RunState, prompt: str | None) -> RunOutcome:
        self._prologue(prompt)
        limits = self._config.limits
        while True:
            self._drain_steering()
            if limits.max_turns is not None and run.turns >= limits.max_turns:
                self._log.append(BudgetExceeded(limit="turns", value=run.turns))
                return self._finish(run, "budget_exceeded")
            outcome = await self._step(run)
            if outcome is not None:
                return outcome

    def _prologue(self, prompt: str | None) -> None:
        events = self._log.read()
        if not events:
            self._log.append(SessionStarted.for_config(self._run_config()))
        for body in repair_bodies(self._repair_planner(events)):
            self._log.append(body)
        if prompt is not None:
            self._log.append(UserMessage(text=prompt))

    def _run_config(self) -> RunConfig:
        return RunConfig(
            provider=self._provider.name,
            model=self._config.model,
            tools=self._tools.names(),
            system_prompt_sha256=hashlib.sha256(self._config.system_prompt.encode()).hexdigest(),
            harness_version=self._harness_version,
            limits=self._config.limits,
        )

    def _drain_steering(self) -> None:
        while self._steering:
            self._log.append(SteerMessage(text=self._steering.popleft()))

    async def _step(self, run: _RunState) -> RunOutcome | None:
        request = build_request(
            self._log.read(),
            model=self._config.model,
            system=self._config.system_prompt,
            tools=self._tools.schemas(),
        )
        request_id = new_id()
        self._log.append(
            ModelRequestStarted(
                request_id=request_id, view="raw", token_estimate=estimate_tokens(request)
            )
        )
        run.turns += 1
        response = await self._stream(run, request, request_id)
        self._log.append(
            ModelRequestFinished(
                request_id=request_id,
                stop_reason=response.stop_reason,
                usage=response.usage,
                raw_usage=response.raw_usage,
            )
        )
        run.usage = run.usage + response.usage
        if response.stop_reason in ("error", "aborted"):
            self._close_out_not_executed(response.calls)
            message = response.error or f"stream ended with stop reason {response.stop_reason}"
            self._log.append(ErrorEvent(kind="provider", retryable=False, message=message))
            return self._finish(run, "error")
        if response.calls:
            await self._dispatch(run, response.calls)
            return None
        if self._steering:
            return None
        return self._finish(run, "done")

    async def _stream(self, run: _RunState, request: ModelRequest, request_id: str) -> _Response:
        calls: list[ToolCall] = []
        usage = Usage()
        raw_usage: dict[str, Any] = {}
        stop_reason: StopReason | None = None
        error: str | None = None
        stream = self._provider.stream(request, run.token)
        try:
            while True:
                try:
                    event = await anext(stream)
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    stop_reason, error = "error", f"{type(exc).__name__}: {exc}"
                    break
                if isinstance(event, ItemDone):
                    self._log.append(ItemEvent(item=event.item, request_id=request_id))
                    if isinstance(event.item, ToolCall):
                        calls.append(event.item)
                elif isinstance(event, UsageReport):
                    usage, raw_usage = event.usage, event.raw
                elif isinstance(event, StreamEnd):
                    stop_reason, error = event.stop_reason, event.error
        finally:
            if isinstance(stream, AsyncGenerator):
                await stream.aclose()
        if stop_reason is None:
            stop_reason, error = "error", "stream ended without a stop reason"
        return _Response(
            stop_reason=stop_reason, usage=usage, raw_usage=raw_usage, error=error, calls=calls
        )

    def _close_out_not_executed(self, calls: Sequence[ToolCall]) -> None:
        for tool_call in calls:
            self._append_result(tool_call.id, "interrupted", NOT_EXECUTED)

    def _append_result(self, call_id: str, status: ToolResultStatus, content: str) -> None:
        self._log.append(
            ItemEvent(item=ToolResult(call_id=call_id, status=status, content=content))
        )

    def _resolve(self, tool_call: ToolCall) -> _Dispatch | str:
        tool = self._tools.get(tool_call.name)
        if tool is None:
            available = ", ".join(self._tools.names()) or "none"
            return f"unknown tool {tool_call.name!r}. Available tools: {available}."
        try:
            args = tool.args_model.model_validate(tool_call.arguments)
        except ValidationError as exc:
            return f"invalid arguments for {tool_call.name}: {_describe_validation(exc)}"
        return _Dispatch(call=tool_call, tool=tool, args=args)

    async def _dispatch(self, run: _RunState, calls: Sequence[ToolCall]) -> None:
        runnable: list[_Dispatch] = []
        for tool_call in calls:
            resolved = self._resolve(tool_call)
            if isinstance(resolved, str):
                self._append_result(tool_call.id, "error", resolved)
            else:
                runnable.append(resolved)
        slots = asyncio.Semaphore(self._config.max_parallel_tools)
        for batch in _batches(runnable):
            async with asyncio.TaskGroup() as group:
                for dispatch in batch:
                    group.create_task(self._run_call(run, dispatch, slots))

    async def _run_call(
        self, run: _RunState, dispatch: _Dispatch, slots: asyncio.Semaphore
    ) -> None:
        async with slots:
            tool, tool_call = dispatch.tool, dispatch.call
            self._log.append(
                ToolCallStarted(
                    call_id=tool_call.id,
                    tool=tool.name,
                    arguments=tool_call.arguments,
                    side_effect=tool.side_effect,
                )
            )
            ctx = ToolContext(
                env=self._env,
                cancel=run.token,
                cwd=self._config.cwd,
                timeout_s=self._config.tool_timeout_s,
                max_output_bytes=self._config.max_tool_output_bytes,
            )
            started = time.monotonic()
            try:
                output = await tool.handler(dispatch.args, ctx)
            except Exception as exc:
                output = ToolOutput(
                    content=f"tool raised {type(exc).__name__}: {exc}",
                    status="error",
                    exit_status="error",
                )
            duration_s = time.monotonic() - started
            self._log.append(
                ToolCallFinished(
                    call_id=tool_call.id,
                    exit_status=output.exit_status,
                    duration_s=duration_s,
                    output_bytes=output.size,
                    truncated=output.truncated,
                )
            )
            self._append_result(tool_call.id, output.status, output.content)

    def _finish(self, run: _RunState, status: RunStatus) -> RunOutcome:
        event = self._log.append(SessionFinished(status=status, turns=run.turns, usage=run.usage))
        return RunOutcome(status=status, turns=run.turns, usage=run.usage, last_seq=event.seq)
