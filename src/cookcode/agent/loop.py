import asyncio
import hashlib
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cookcode import __version__
from cookcode.agent.approval import Approver, Decision, PolicyOnlyApprover, Risk
from cookcode.agent.context import (
    SUMMARIZE_VIEW,
    build_request,
    dropped_reasoning,
    estimate_tokens,
    items_sha256,
    summary_cut,
    token_ledger,
    transcript,
)
from cookcode.agent.gate import (
    PLAN_RULE,
    Mode,
    Policy,
    covers,
    decide,
    mode_at,
    parse_rule,
    session_rules,
    subject,
)
from cookcode.agent.hooks import Hook, HookContext
from cookcode.agent.repair import NOT_EXECUTED, RepairAction, plan_repairs, repair_bodies
from cookcode.agent.tools import (
    EXIT_PLAN,
    Tool,
    ToolContext,
    ToolOutput,
    ToolRegistry,
    loaded_tools,
    workspace_path,
)
from cookcode.core.cancel import CancelToken
from cookcode.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    BudgetExceeded,
    CheckpointCreated,
    CompactionApplied,
    ContextSource,
    ErrorEvent,
    Event,
    HookBlocked,
    Interrupted,
    ItemEvent,
    Limits,
    ModeChanged,
    ModelRequestFinished,
    ModelRequestStarted,
    ProviderRetry,
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
from cookcode.core.ids import new_id
from cookcode.core.items import Message, ToolCall, ToolResult, ToolResultStatus, text_message
from cookcode.core.strategy import Strategy
from cookcode.core.usage import StopReason, Usage
from cookcode.env.base import Environment
from cookcode.providers.base import (
    ItemDone,
    ModelRequest,
    Provider,
    StreamEnd,
    StreamRetry,
    UsageReport,
)
from cookcode.store.base import CheckpointStore, SessionLog

RunStatus = Literal["done", "error", "budget_exceeded", "interrupted"]
Phase = Literal["streaming", "tool", "idle"]

WALL_CLOCK = "wall_clock"
SUMMARIZER_SYSTEM = (
    "You compress the history of a coding agent's session so it can keep working with less "
    "context. Write for the agent itself, not for a user."
)
SUMMARIZER_PROMPT = (
    "Summarize the session below. Keep: the task and any constraints, files read or changed and "
    "what changed, commands run and what they showed, errors and how they were resolved, what "
    "is done, and what remains. Keep exact paths, names and values. Drop pleasantries and "
    "anything superseded. Plain text, no preamble.\n\n"
)
KILLED_READ_ONLY = (
    "[killed by an interrupt before it finished. It is read-only, so it is safe to retry.]"
)
KILLED_MUTATING = (
    "[killed by an interrupt before it finished. It may have changed state, "
    "so inspect before retrying.]"
)
NO_REASON = "the approver gave no reason"
PLAN_REVIEW = "the agent asks to leave plan mode and carry out this plan"
PLAN_REJECTED = (
    "plan not approved: {reason}. You are still in plan mode. Revise the plan and call "
    "exit_plan again."
)
CWD_MOVED = (
    "not run: the working directory changed to {cwd} after this call was gated, "
    "so its paths now resolve differently. Issue the call again."
)
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
    strategy: Strategy = Strategy()
    max_stop_blocks: int = Field(default=3, ge=0)
    context_window: int | None = None
    context_source: ContextSource | None = None


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
    stop_blocks: int = 0
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
    text: str = ""


@dataclass(frozen=True)
class _Dispatch:
    call: ToolCall
    tool: Tool[Any]
    args: BaseModel
    gated_cwd: str | None = None

    def subjects(self, root: str, cwd: str) -> list[str]:
        touched = self.tool.touches(self.args) if self.tool.touches is not None else []
        if touched:
            return [workspace_path(root, cwd, path) for path in touched]
        return [subject(self.tool.name, self.args.model_dump(), cwd, root)]


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
        checkpoints: CheckpointStore | None = None,
        hooks: Sequence[Hook] = (),
    ) -> None:
        if config.limits.max_cost_usd is not None:
            raise ValueError("cost limits need pricing, which arrives in M3")
        self._log = log
        self._provider = provider
        self._env = env
        if config.strategy.masks and tools.get("recall") is None:
            raise ValueError(
                f"strategy {config.strategy.label} masks tool outputs and needs a recall tool"
            )
        self._tools = tools
        self._config = config
        self._harness_version = harness_version
        self._repair_planner = repair_planner
        self._approver: Approver = approver or PolicyOnlyApprover()
        self._checkpoints = checkpoints
        self._hooks = tuple(hooks)
        self._steering: deque[str] = deque()
        self._active = False
        self._mode: Mode = config.mode

    @property
    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        """Takes effect at the next run, which logs it."""
        if self._active:
            raise RuntimeError("the mode cannot change while Agent.run() is active")
        self._mode = mode

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
                await self._run_finish_hooks(run)
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
        previous = mode_at(events)
        if not events:
            self._log.append(SessionStarted.for_config(self._run_config()))
        self._log.append(RunStarted.for_config(self._run_config()))
        for body in repair_bodies(self._repair_planner(events)):
            self._log.append(body)
        if (previous == "plan") != (self._mode == "plan"):
            self._log.append(ModeChanged(mode=self._mode, previous=previous, source="run"))
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
            mode=self._mode,
            policy=(
                *(f"allow {rule.display}" for rule in self._config.policy.allow),
                *(f"deny {rule.display}" for rule in self._config.policy.deny),
            ),
            strategy=self._config.strategy,
            hooks=tuple(hook.name for hook in self._hooks),
            context_window=self._config.context_window,
            context_source=self._config.context_source,
        )

    def _hook_context(self, run: _RunState) -> HookContext:
        async def ask(view: str, system: str, prompt: str) -> str | None:
            request = ModelRequest(
                model=self._config.model, system=system, items=(text_message("user", prompt),)
            )
            response = await self._side_call(run, view, request)
            ok = response.stop_reason == "end_turn" and response.text.strip()
            return response.text.strip() if ok else None

        return HookContext(
            env=self._env,
            cancel=run.token,
            cwd=self._current_cwd(),
            timeout_s=self._config.tool_timeout_s,
            max_output_bytes=self._config.max_tool_output_bytes,
            history=self._log.read(),
            ask=ask,
        )

    async def _run_finish_hooks(self, run: _RunState) -> None:
        for hook in self._hooks:
            if hook.on_finish is not None and not run.cancelled():
                await hook.on_finish(self._hook_context(run))

    async def _side_call(self, run: _RunState, view: str, request: ModelRequest) -> _Response:
        """A model call outside the conversation (a summary, a memory pass): logged with its
        view and usage, its items never logged, so the context builder never sees it."""
        request_id = new_id()
        self._log.append(
            ModelRequestStarted(
                request_id=request_id, view=view, token_estimate=estimate_tokens(request)
            )
        )
        response = await self._stream(run, request, request_id, log_items=False)
        self._log.append(
            ModelRequestFinished(
                request_id=request_id,
                stop_reason=response.stop_reason,
                usage=response.usage,
                raw_usage=response.raw_usage,
            )
        )
        run.usage = run.usage + response.usage
        return response

    async def _blocked_call(self, run: _RunState, tool_call: ToolCall) -> bool:
        for hook in self._hooks:
            if hook.before_tool is None:
                continue
            message = await hook.before_tool(tool_call, self._hook_context(run))
            if message is not None:
                self._log.append(
                    HookBlocked(hook=hook.name, point="tool", message=message, call_id=tool_call.id)
                )
                self._append_result(tool_call.id, "error", f"blocked by {hook.name}: {message}")
                return True
        return False

    async def _blocked_stop(self, run: _RunState) -> bool:
        """A hook may send the model back to work instead of ending the turn, at most
        max_stop_blocks times per run, so a check that never passes cannot loop forever."""
        if run.stop_blocks >= self._config.max_stop_blocks:
            return False
        for hook in self._hooks:
            if hook.before_stop is None:
                continue
            message = await hook.before_stop(self._hook_context(run))
            if message is not None:
                run.stop_blocks += 1
                self._log.append(HookBlocked(hook=hook.name, point="stop", message=message))
                return True
        return False

    def _current_cwd(self) -> str:
        return _cwd(self._log.read(), self._config.cwd)

    def _drain_steering(self) -> None:
        while self._steering:
            self._log.append(SteerMessage(text=self._steering.popleft()))

    def _build(self, events: Sequence[Event]) -> ModelRequest:
        return build_request(
            events,
            model=self._config.model,
            system=self._config.system_prompt,
            tools=self._tools.schemas(loaded_tools(events), plan=mode_at(events) == "plan"),
            provider=self._provider.name,
            strategy=self._config.strategy,
        )

    async def _summarize_if_needed(self, run: _RunState) -> None:
        """Summarize older steps when the request would pass the trigger. The summary is
        logged in `compaction_applied`, so the builder stays a pure function of the log."""
        strategy = self._config.strategy
        events = self._log.read()
        before = estimate_tokens(self._build(events))
        if not strategy.summarizes or before <= strategy.trigger_tokens:
            return
        cut = summary_cut(events, strategy.keep_steps)
        if cut is None:
            return
        request = ModelRequest(
            model=self._config.model,
            system=SUMMARIZER_SYSTEM,
            items=(text_message("user", SUMMARIZER_PROMPT + transcript(events, *cut)),),
        )
        response = await self._side_call(run, SUMMARIZE_VIEW, request)
        if response.stop_reason != "end_turn" or not response.text.strip():
            return
        draft = CompactionApplied(
            strategy=strategy.label,
            from_seq=cut[0],
            to_seq=cut[1],
            tokens_before=before,
            tokens_after=0,
            summary=response.text.strip(),
        )
        probe = Event(
            seq=events[-1].seq + 2, id=new_id(), ts=events[-1].ts, layer="internal", body=draft
        )
        after = estimate_tokens(self._build([*self._log.read(), probe]))
        self._log.append(draft.model_copy(update={"tokens_after": after}))

    async def _step(self, run: _RunState) -> RunOutcome | None:
        await self._summarize_if_needed(run)
        if run.cancelled():
            return self._end_cancelled(run, "streaming", ())
        events = self._log.read()
        provider, model = self._provider.name, self._config.model
        request = self._build(events)
        request_id = new_id()
        self._log.append(
            ModelRequestStarted(
                request_id=request_id,
                view=self._config.strategy.label,
                token_estimate=estimate_tokens(request),
                items_sha256=items_sha256(request.items),
                reasoning_dropped=dropped_reasoning(events, provider=provider, model=model),
                ledger=token_ledger(request),
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
        if self._steering or await self._blocked_stop(run):
            return None
        await self._run_finish_hooks(run)
        return self._finish(run, "done")

    async def _stream(
        self, run: _RunState, request: ModelRequest, request_id: str, *, log_items: bool = True
    ) -> _Response:
        calls: list[ToolCall] = []
        text: list[str] = []
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
                    if log_items:
                        self._log.append(ItemEvent(item=event.item, request_id=request_id))
                    if isinstance(event.item, ToolCall):
                        calls.append(event.item)
                    elif isinstance(event.item, Message):
                        text.append(event.item.text)
                elif isinstance(event, UsageReport):
                    usage, raw_usage = event.usage, event.raw
                elif isinstance(event, StreamRetry):
                    self._log.append(
                        ProviderRetry(
                            request_id=request_id,
                            attempt=event.attempt,
                            delay_s=event.delay_s,
                            reason=event.reason,
                            status=event.status,
                        )
                    )
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
            stop_reason=stop_reason,
            usage=usage,
            raw_usage=raw_usage,
            error=error,
            calls=calls,
            text="".join(text),
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
        if tool_call.raw_arguments is not None:
            return (
                f"invalid arguments for {tool_call.name}: not a JSON object: "
                f"{tool_call.raw_arguments[:200]!r}. Send the arguments as one JSON object."
            )
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
            elif not await self._blocked_call(run, tool_call):
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
        cwd = self._current_cwd()
        for index, dispatch in enumerate(runnable):
            verdict = None if run.cancelled() else await self._gate(run, dispatch, cwd)
            if verdict is None:
                return approved, list(runnable[index:])
            if verdict:
                approved.append(replace(dispatch, gated_cwd=cwd))
        return approved, []

    async def _gate(self, run: _RunState, dispatch: _Dispatch, cwd: str) -> bool | None:
        tool, tool_call = dispatch.tool, dispatch.call
        if tool.name == EXIT_PLAN:
            return await self._gate_exit_plan(run, dispatch, cwd)
        decision = decide(
            tool=tool,
            arguments=tool_call.arguments,
            cwd=cwd,
            root=self._env.root,
            mode=self._mode,
            policy=self._config.policy,
            session_allow=session_rules(self._log.read()),
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
        risk = Risk(
            side_effect=tool.side_effect,
            rule=decision.rule,
            reason=decision.reason,
            cwd=cwd,
            subjects=tuple(dispatch.subjects(self._env.root, cwd)),
        )
        answer = await self._ask(run, tool_call, risk)
        if answer is None:
            return None
        if answer.action == "allow_always":
            answer = self._narrow(dispatch, answer, cwd)
        self._log.append(
            ApprovalResolved(
                call_id=tool_call.id, decision=answer.action, reason=answer.reason, rule=answer.rule
            )
        )
        if answer.action == "deny":
            self._append_result(tool_call.id, "denied", f"denied: {answer.reason or NO_REASON}")
            return False
        return True

    async def _gate_exit_plan(self, run: _RunState, dispatch: _Dispatch, cwd: str) -> bool | None:
        """The user reviews the plan. Allow leaves plan mode for ask, allow_always for
        auto-edit, and a denial's reason goes back to the model as feedback."""
        tool_call = dispatch.call
        if self._mode != "plan":
            message = f"not in plan mode: the mode is {self._mode}"
            self._append_result(tool_call.id, "error", message)
            return False
        self._log.append(
            ApprovalRequested(call_id=tool_call.id, risk=dispatch.tool.side_effect, rule=PLAN_RULE)
        )
        risk = Risk(
            side_effect=dispatch.tool.side_effect,
            rule=PLAN_RULE,
            reason=PLAN_REVIEW,
            cwd=cwd,
            subjects=(),
        )
        answer = await self._ask(run, tool_call, risk)
        if answer is None:
            return None
        if answer.action == "deny":
            self._log.append(
                ApprovalResolved(
                    call_id=tool_call.id, decision="deny", reason=answer.reason, rule=PLAN_RULE
                )
            )
            reason = PLAN_REJECTED.format(reason=answer.reason or NO_REASON)
            self._append_result(tool_call.id, "denied", reason)
            return False
        mode: Mode = "auto-edit" if answer.action == "allow_always" else "ask"
        self._log.append(
            ApprovalResolved(
                call_id=tool_call.id,
                decision="allow",
                reason=f"plan approved into {mode} mode",
                rule=PLAN_RULE,
            )
        )
        self._log.append(
            ModeChanged(mode=mode, previous="plan", source="exit_plan", call_id=tool_call.id)
        )
        self._mode = mode
        return True

    def _narrow(self, dispatch: _Dispatch, answer: Decision, cwd: str) -> Decision:
        rule = parse_rule(answer.rule)
        if rule is not None and covers(
            rule,
            tool=dispatch.tool,
            arguments=dispatch.call.arguments,
            cwd=cwd,
            root=self._env.root,
            policy=self._config.policy,
        ):
            return answer
        return Decision(
            action="allow",
            reason=f"allow_always rule {answer.rule} does not cover this call; allowed once",
        )

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

    async def _snapshot(
        self,
        store: CheckpointStore,
        touches: Callable[[Any], list[str]],
        args: BaseModel,
        cwd: str,
    ) -> str | None:
        try:
            root = self._env.root
            paths = list(dict.fromkeys(workspace_path(root, cwd, path) for path in touches(args)))
            if not paths:
                return None
            files: dict[str, bytes | None] = {}
            for path in paths:
                try:
                    files[path] = await self._env.read_file(path)
                except FileNotFoundError:
                    files[path] = None
            checkpoint_id = new_id()
            store.save(checkpoint_id, files)
        except asyncio.CancelledError:
            if _hard_cancelled():
                raise
            return "snapshot failed: CancelledError: "
        except Exception as exc:
            return f"snapshot failed: {type(exc).__name__}: {exc}"
        turn = sum(1 for event in self._log.read() if isinstance(event.body, UserMessage))
        self._log.append(
            CheckpointCreated(checkpoint_id=checkpoint_id, paths=tuple(paths), turn=turn)
        )
        return None

    async def _run_call(
        self, run: _RunState, dispatch: _Dispatch, slots: asyncio.Semaphore, killed: set[str]
    ) -> None:
        async with slots:
            tool, tool_call = dispatch.tool, dispatch.call
            if run.cancelled():
                self._append_result(tool_call.id, "interrupted", NOT_EXECUTED)
                return
            cwd = self._current_cwd()
            gated = dispatch.gated_cwd
            root = self._env.root
            if gated is not None and dispatch.subjects(root, gated) != dispatch.subjects(root, cwd):
                self._append_result(tool_call.id, "error", CWD_MOVED.format(cwd=cwd))
                return
            if tool.touches is not None and self._checkpoints is not None:
                failure = await self._snapshot(self._checkpoints, tool.touches, dispatch.args, cwd)
                if failure is not None:
                    self._append_result(tool_call.id, "error", failure)
                    return
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
                cwd=cwd,
                timeout_s=self._config.tool_timeout_s,
                max_output_bytes=self._config.max_tool_output_bytes,
                history=self._log.read,
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
