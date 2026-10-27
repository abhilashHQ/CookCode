"""Hooks: checks the loop runs before a tool call and before it ends a turn. A hook returns None
to let the loop go on, or a message: a blocked tool call gets it as its error result, and a
blocked stop gets it as a note to the model, which then keeps working. Each block is logged as
`hook_blocked`, so the context builder rebuilds the same request from the log.

on_finish runs once when a run ends with the model done or a budget hit, before
`session_finished`. It may make side requests through `ask` (view, system, prompt), which are
logged under that view and never become part of the conversation."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from harness.core.cancel import CancelToken
from harness.core.events import Event
from harness.core.items import ToolCall
from harness.env.base import Environment


@dataclass(frozen=True)
class HookContext:
    env: Environment
    cancel: CancelToken
    cwd: str
    timeout_s: float
    max_output_bytes: int
    history: Sequence[Event]
    ask: Callable[[str, str, str], Awaitable[str | None]] | None = None


@dataclass(frozen=True)
class Hook:
    name: str
    before_tool: Callable[[ToolCall, HookContext], Awaitable[str | None]] | None = None
    before_stop: Callable[[HookContext], Awaitable[str | None]] | None = None
    on_finish: Callable[[HookContext], Awaitable[None]] | None = None
