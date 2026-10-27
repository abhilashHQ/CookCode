"""Verify-first: reproduce the bug before fixing it, and prove the fix against the reproduction.

The `repro` tool registers a command that must fail now. Until one is registered, the hook lets
file tools create new files (the test or script that reproduces the bug) but not change existing
ones. When the model tries to end its turn, the hook re-runs the latest registered reproduction
and sends the model back to work while it still fails. The registration is read from the log
(the latest ok result of a repro call), so there is no state outside it. Bash can still change
files before a reproduction exists; the hook covers the file tools only."""

import shlex
from collections.abc import Sequence

from pydantic import BaseModel, Field

from cookcode.agent.hooks import Hook, HookContext
from cookcode.agent.tools import Tool, ToolContext, ToolOutput, workspace_path
from cookcode.core.events import Event, ItemEvent, ToolCallFinished
from cookcode.core.items import ToolCall, ToolResult
from cookcode.env.base import ExecResult

HOOK_NAME = "verify-first"
CD_FAILED_EXIT = 97
MESSAGE_TAIL = 2000

DESCRIPTION = (
    "Register the command that reproduces the bug you are fixing, such as a test or a script "
    "that checks the symptom from the task. It runs now and must fail; once registered, it is "
    "re-run when you finish, and your turn ends only when it passes. Register a corrected "
    "command at any time if the first one was wrong. Until a reproduction is registered, edit "
    "and write may only create new files."
)


class ReproArgs(BaseModel):
    command: str = Field(description="Shell command that fails while the bug is present.")


async def _run(command: str, cwd: str, ctx: ToolContext | HookContext) -> ExecResult:
    target = cwd if cwd.startswith("/") else f"./{cwd}"
    script = f"cd -P -- {shlex.quote(target)} 2>/dev/null || exit {CD_FAILED_EXIT}\n{command}"
    return await ctx.env.exec(
        script, cwd=".", timeout_s=ctx.timeout_s, max_output_bytes=ctx.max_output_bytes,
        cancel=ctx.cancel,
    )


async def _repro(args: ReproArgs, ctx: ToolContext) -> ToolOutput:
    try:
        result = await _run(args.command, ctx.cwd, ctx)
    except Exception as exc:
        return ToolOutput(content=f"repro failed to run: {type(exc).__name__}: {exc}",
                          status="error", exit_status="error")
    output = result.output.rstrip("\n")
    if result.timed_out or result.cancelled:
        note = "it timed out" if result.timed_out else "it was interrupted"
        return ToolOutput(content=f"not registered: {note}. A reproduction must finish quickly.",
                          status="error", exit_status="error")
    if result.exit_code == 0:
        return ToolOutput(
            content=(
                "not registered: the command passes, so it does not reproduce the bug. It must "
                "fail while the bug is present, on the symptom the task describes.\n" + output
            ).rstrip("\n"),
            status="error",
            exit_status="error",
        )
    return ToolOutput(
        content=f"registered (exit code {result.exit_code}). It runs again when you finish.\n"
        + output,
        cwd=ctx.cwd,
    )


def repro_tool() -> Tool[ReproArgs]:
    return Tool(name="repro", description=DESCRIPTION, args_model=ReproArgs,
                side_effect="mutating", handler=_repro)


def registered(history: Sequence[Event]) -> tuple[str, str] | None:
    """The latest reproduction that registered: its command and the directory it ran in."""
    calls: dict[str, str] = {}
    cwds: dict[str, str] = {}
    latest: tuple[str, str] | None = None
    for event in history:
        body = event.body
        if isinstance(body, ItemEvent) and isinstance(body.item, ToolCall):
            if body.item.name == "repro":
                calls[body.item.id] = str(body.item.arguments.get("command", ""))
        elif isinstance(body, ToolCallFinished) and body.call_id in calls and body.cwd:
            cwds[body.call_id] = body.cwd
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolResult):
            result = body.item
            if result.call_id in calls and result.status == "ok":
                latest = (calls[result.call_id], cwds.get(result.call_id, "."))
    return latest


def _changed_files(history: Sequence[Event]) -> bool:
    file_calls = {
        body.item.id
        for body in (event.body for event in history)
        if isinstance(body, ItemEvent)
        and isinstance(body.item, ToolCall)
        and body.item.name in ("edit", "write")
    }
    return any(
        isinstance(event.body, ItemEvent)
        and isinstance(event.body.item, ToolResult)
        and event.body.item.call_id in file_calls
        and event.body.item.status == "ok"
        for event in history
    )


async def _before_tool(call: ToolCall, ctx: HookContext) -> str | None:
    if call.name not in ("edit", "write") or registered(ctx.history) is not None:
        return None
    if call.name == "write":
        path = workspace_path(ctx.env.root, ctx.cwd, str(call.arguments.get("path", "")))
        probe = await ctx.env.exec(
            f"test -e {shlex.quote(path)}", cwd=".", timeout_s=ctx.timeout_s,
            max_output_bytes=1000, cancel=ctx.cancel,
        )
        if probe.exit_code != 0:
            return None
    return (
        "register a failing reproduction with repro before changing existing files. Create a "
        "new test or script that shows the bug first, if you need one."
    )


async def _before_stop(ctx: HookContext) -> str | None:
    repro = registered(ctx.history)
    if repro is None:
        if not _changed_files(ctx.history):
            return None
        return (
            "you changed files without registering a reproduction. Register a command that "
            "fails on the original bug with repro, then make it pass."
        )
    command, cwd = repro
    result = await _run(command, cwd, ctx)
    if result.exit_code == 0:
        return None
    state = "timed out" if result.timed_out else f"exit code {result.exit_code}"
    tail = result.output.rstrip("\n")[-MESSAGE_TAIL:]
    return (
        f"the reproduction still fails ({state}): {command}\n{tail}\n"
        "Keep fixing until it passes. If the reproduction itself is wrong, register a corrected "
        "one with repro."
    )


def verify_first_hook() -> Hook:
    return Hook(name=HOOK_NAME, before_tool=_before_tool, before_stop=_before_stop)
