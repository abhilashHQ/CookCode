import asyncio
import hashlib
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness import __version__
from harness.agent.approval import Approver, Decision, PolicyOnlyApprover, Risk
from harness.agent.context import build_request, estimate_tokens
from harness.agent.gate import Mode, Policy, decide, session_rules
from harness.agent.repair import NOT_EXECUTED, RepairAction, plan_repairs, repair_bodies
from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.cancel import CancelToken
from harness.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    BudgetExceeded,
    ErrorEvent,
    Event,
    Interrupted,
    ItemEvent,
    Limits,
    ModelRequestFinished,
    ModelRequestStarted,
    RunConfig,
    RunStarted,
    SessionFinished,
    SessionStarted,
    SideEffect,
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
Phase = Literal["streaming", "tool", "idle"]

WALL_CLOCK = "wall_clock"
KILLED_READ_ONLY = (
    "[killed by an interrupt before it finished. It is read-only, so it is safe to retry.]"
)
KILLED_MUTATING = (
    "[killed by an interrupt before it finished. It may have changed state, "
    "so inspect before retrying.]"
)
NO_REASON = "the approver gave no reason"
TIMEOUT_MUTATING = (
    "[killed by the harness at the tool timeout. It may have changed state, "
    "so inspect before retrying.]"
)


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    system_prompt: str
    limits: Limits = Limits(max_turns=50)
    tool_timeout_s: float = Field(default=120.0, gt=0)
    max_tool_output_bytes: int = Field(default=30_000, ge=1)
    max_parallel_tools: int = Field(default=8, ge=1)
    cwd: str = "."
    mode: Mode = "ask"
    policy: Policy = Policy()


class RunOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RunStatus
    turns: int
    usage: Usage
    last_seq: int


@dataclass
class _RunState:
    token: CancelToken
    caller: CancelToken | None
    started_at: float
    turns: int = 0
    usage: Usage = field(default_factory=Usage)

    def cancelled(self) -> bool:
        if self.caller is not None and self.caller.cancelled:
            self.token.cancel(self.caller.reason or "user")
        return self.token.cancelled


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


def _tool_result(side_effect: SideEffect, output: ToolOutput) -> tuple[ToolResultStatus, str]:
    status: ToolResultStatus
    if output.exit_status == "killed":
        if side_effect == "read_only":
            status, note = "interrupted", KILLED_READ_ONLY
        else:
            status, note = "unknown", KILLED_MUTATING
    elif output.exit_status == "timeout" and side_effect != "read_only":
        status, note = "unknown", TIMEOUT_MUTATING
    else:
        return output.status, output.content
    return status, f"{output.content}\n{note}" if output.content else note


def _hard_cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def _link(source: CancelToken, target: CancelToken) -> None:
    await source.wait()
    target.cancel(source.reason or "user")


async def _watchdog(seconds: float, target: CancelToken) -> None:
    await asyncio.sleep(seconds)
    target.cancel(WALL_CLOCK)


def _cwd(events: Sequence[Event], default: str) -> str:
    for event in reversed(events):
        body = event.body
        if isinstance(body, ToolCallFinished) and body.cwd is not None:
            return body.cwd
    return default


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
        approver: Approver | None = None,
    ) -> None:
        if config.limits.max_cost_usd is not None:
            raise ValueError("cost limits need pricing, which arrives in M3")
        self._log = log
        self._provider = provider
        self._env = env
        self._tools = tools
        self._config = config
        self._harness_version = harness_version
        self._repair_planner = repair_planner
        self._approver: Approver = approver or PolicyOnlyApprover()
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
        run = _RunState(token=CancelToken(), caller=cancel, started_at=time.monotonic())
        watchers: list[asyncio.Task[None]] = []
        try:
            if cancel is not None:
                watchers.append(asyncio.create_task(_link(cancel, run.token)))
            if self._config.limits.max_wall_s is not None:
                watchers.append(
                    asyncio.create_task(_watchdog(self._config.limits.max_wall_s, run.token))
                )
            return await self._execute(run, prompt)
        finally:
            try:
                for watcher in watchers:
                    watcher.cancel()
                if watchers:
                    await asyncio.gather(*watchers, return_exceptions=True)
            finally:
                self._active = False

    async def _execute(self, run: _RunState, prompt: str | None) -> RunOutcome:
        self._prologue(prompt)
        while True:
            self._drain_steering()
            if run.cancelled():
                return self._end_cancelled(run, "idle", ())
            exceeded = self._budget_exceeded(run)
            if exceeded is not None:
                self._log.append(exceeded)
                return self._finish(run, "budget_exceeded")
            outcome = await self._step(run)
            if outcome is not None:
                return outcome

    def _budget_exceeded(self, run: _RunState) -> BudgetExceeded | None:
        limits = self._config.limits
        if limits.max_turns is not None and run.turns >= limits.max_turns:
            return BudgetExceeded(limit="turns", value=run.turns)
        spent = run.usage.total_input + run.usage.output
        if limits.max_tokens is not None and spent >= limits.max_tokens:
            return BudgetExceeded(limit="tokens", value=spent)
        return None

    def _prologue(self, prompt: str | None) -> None:
        events = self._log.read()
        if not events:
            self._log.append(SessionStarted.for_config(self._run_config()))
        self._log.append(RunStarted.for_config(self._run_config()))
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

    def _current_cwd(self) -> str:
        return _cwd(self._log.read(), self._config.cwd)

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
        if response.stop_reason in ("aborted", "error") and run.cancelled():
            self._close_out_not_executed(response.calls)
            return self._end_cancelled(run, "streaming", ())
        if response.stop_reason in ("error", "aborted"):
            self._close_out_not_executed(response.calls)
            message = response.error or f"stream ended with stop reason {response.stop_reason}"
            self._log.append(ErrorEvent(kind="provider", retryable=False, message=message))
            return self._finish(run, "error")
        if response.calls:
            killed = await self._dispatch(run, response.calls)
            if run.cancelled():
                return self._end_cancelled(run, "tool", killed)
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
                except asyncio.CancelledError:
                    if _hard_cancelled():
                        raise
                    stop_reason, error = "error", "provider raised CancelledError"
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
                    break
        finally:
            if isinstance(stream, AsyncGenerator):
                try:
                    await stream.aclose()
                except Exception as exc:
                    if stop_reason is None:
                        stop_reason, error = "error", f"{type(exc).__name__}: {exc}"
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

    async def _dispatch(self, run: _RunState, calls: Sequence[ToolCall]) -> tuple[str, ...]:
        runnable: list[_Dispatch] = []
        for tool_call in calls:
            resolved = self._resolve(tool_call)
            if isinstance(resolved, str):
                self._append_result(tool_call.id, "error", resolved)
            else:
                runnable.append(resolved)
        approved, ungated = await self._gate_all(run, runnable)
        if ungated:
            pending = {dispatch.call.id for dispatch in (*approved, *ungated)}
            self._close_out_not_executed([c for c in calls if c.id in pending])
            return ()
        killed: set[str] = set()
        slots = asyncio.Semaphore(self._config.max_parallel_tools)
        for batch in _batches(approved):
            if run.cancelled():
                self._close_out_not_executed([dispatch.call for dispatch in batch])
                continue
            async with asyncio.TaskGroup() as group:
                for dispatch in batch:
                    group.create_task(self._run_call(run, dispatch, slots, killed))
        return tuple(tool_call.id for tool_call in calls if tool_call.id in killed)

    async def _gate_all(
        self, run: _RunState, runnable: Sequence[_Dispatch]
    ) -> tuple[list[_Dispatch], list[_Dispatch]]:
        approved: list[_Dispatch] = []
        for index, dispatch in enumerate(runnable):
            verdict = None if run.cancelled() else await self._gate(run, dispatch)
            if verdict is None:
                return approved, list(runnable[index:])
            if verdict:
                approved.append(dispatch)
        return approved, []

    async def _gate(self, run: _RunState, dispatch: _Dispatch) -> bool | None:
        tool, tool_call = dispatch.tool, dispatch.call
        events = self._log.read()
        decision = decide(
            tool=tool,
            arguments=tool_call.arguments,
            cwd=_cwd(events, self._config.cwd),
            mode=self._config.mode,
            policy=self._config.policy,
            session_allow=session_rules(events),
        )
        if decision.action == "allow":
            by_mode = decision.rule is not None and decision.rule.startswith("mode:")
            if not by_mode or tool.side_effect != "read_only":
                self._log.append(
                    ApprovalResolved(call_id=tool_call.id, decision="allow", rule=decision.rule)
                )
            return True
        if decision.action == "deny":
            self._log.append(
                ApprovalResolved(
                    call_id=tool_call.id,
                    decision="deny",
                    reason=decision.reason,
                    rule=decision.rule,
                )
            )
            self._append_result(tool_call.id, "denied", f"denied: {decision.reason}")
            return False
        self._log.append(
            ApprovalRequested(call_id=tool_call.id, risk=tool.side_effect, rule=decision.rule)
        )
        risk = Risk(side_effect=tool.side_effect, rule=decision.rule, reason=decision.reason)
        answer = await self._ask(run, tool_call, risk)
        if answer is None:
            return None
        self._log.append(
            ApprovalResolved(
                call_id=tool_call.id, decision=answer.action, reason=answer.reason, rule=answer.rule
            )
        )
        if answer.action == "deny":
            self._append_result(tool_call.id, "denied", f"denied: {answer.reason or NO_REASON}")
            return False
        return True

    async def _ask(self, run: _RunState, tool_call: ToolCall, risk: Risk) -> Decision | None:
        asking = asyncio.ensure_future(self._approver.decide(tool_call, risk, run.token))
        stopping = asyncio.ensure_future(run.token.wait())
        try:
            await asyncio.wait({asking, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            asking.cancel()
            stopping.cancel()
            await asyncio.gather(asking, stopping, return_exceptions=True)
        if run.cancelled():
            return None
        if asking.cancelled():
            return Decision(action="deny", reason="approver failed: CancelledError: ")
        error = asking.exception()
        if error is None:
            return asking.result()
        if not isinstance(error, Exception):
            raise error
        return Decision(action="deny", reason=f"approver failed: {type(error).__name__}: {error}")

    async def _run_call(
        self, run: _RunState, dispatch: _Dispatch, slots: asyncio.Semaphore, killed: set[str]
    ) -> None:
        async with slots:
            tool, tool_call = dispatch.tool, dispatch.call
            if run.cancelled():
                self._append_result(tool_call.id, "interrupted", NOT_EXECUTED)
                return
            self._log.append(
                ToolCallStarted(
                    call_id=tool_call.id,
                    tool=tool.name,
                    arguments=tool_call.arguments,
                    side_effect=tool.side_effect,
                ),
                durable=True,
            )
            ctx = ToolContext(
                env=self._env,
                cancel=run.token,
                cwd=self._current_cwd(),
                timeout_s=self._config.tool_timeout_s,
                max_output_bytes=self._config.max_tool_output_bytes,
            )
            started = time.monotonic()
            try:
                output = await tool.handler(dispatch.args, ctx)
            except asyncio.CancelledError:
                if _hard_cancelled():
                    raise
                output = ToolOutput(
                    content="tool raised CancelledError",
                    status="error",
                    exit_status="killed" if run.cancelled() else "error",
                )
            except Exception as exc:
                output = ToolOutput(
                    content=f"tool raised {type(exc).__name__}: {exc}",
                    status="error",
                    exit_status="error",
                )
            duration_s = time.monotonic() - started
            if run.cancelled():
                killed.add(tool_call.id)
            self._log.append(
                ToolCallFinished(
                    call_id=tool_call.id,
                    exit_status=output.exit_status,
                    duration_s=duration_s,
                    output_bytes=output.size,
                    truncated=output.truncated,
                    cwd=output.cwd,
                )
            )
            self._append_result(tool_call.id, *_tool_result(tool.side_effect, output))

    def _end_cancelled(self, run: _RunState, phase: Phase, killed: tuple[str, ...]) -> RunOutcome:
        if run.token.reason == WALL_CLOCK:
            elapsed = time.monotonic() - run.started_at
            self._log.append(BudgetExceeded(limit="wall_clock", value=elapsed))
            return self._finish(run, "budget_exceeded")
        self._log.append(Interrupted(phase=phase, killed=killed))
        return self._finish(run, "interrupted")

    def _finish(self, run: _RunState, status: RunStatus) -> RunOutcome:
        self._drain_steering()
        event = self._log.append(SessionFinished(status=status, turns=run.turns, usage=run.usage))
        return RunOutcome(status=status, turns=run.turns, usage=run.usage, last_seq=event.seq)
