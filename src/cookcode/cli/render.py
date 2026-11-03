"""The interactive client's renderer (decision 0053). Finished things are printed once into the
terminal's own scrollback: prompts, answers, one block per tool call when its result arrives,
and the plan under review. What is still changing (the streaming answer, calls in flight) is
kept as state for the live region at the bottom of the screen to draw. It observes the same
two streams as every other client (0026)."""

import difflib
import io
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cookcode.agent.context import SIDE_VIEWS
from cookcode.agent.tools import EXIT_PLAN
from cookcode.core.events import (
    BudgetExceeded,
    CheckpointRestored,
    CompactionApplied,
    ErrorEvent,
    Event,
    HookBlocked,
    Interrupted,
    ItemEvent,
    ModeChanged,
    ModelRequestFinished,
    ModelRequestStarted,
    ProviderRetry,
    Repaired,
    SteerMessage,
    ToolCallStarted,
    UserMessage,
)
from cookcode.core.items import Message, Reasoning, ToolCall, ToolResult
from cookcode.core.usage import Usage
from cookcode.providers.base import ItemDelta, ItemStarted, StreamEvent

DOT = "●"
ELBOW = "⎿"
PREVIEW = 4
REMOVED = "on #4b1d1d"
ADDED = "on #1c3b23"
NAMES = {
    "read": "Read", "write": "Write", "edit": "Update", "bash": "Bash", "glob": "Glob",
    "grep": "Search", "recall": "Recall", "job_start": "Job", "job_check": "JobCheck",
    "task": "Task", "tool_search": "ToolSearch", "skill": "Skill",
}
MODE_NOTES = {
    "plan": "plan mode on: read-only until you approve a plan",
    "ask": "plan mode off: asking before edits",
    "auto-edit": "plan mode off: accepting edits",
    "yolo": "plan mode off: everything runs",
}


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def call_label(call: ToolCall) -> str:
    """`Read(src/a.py)`, `Bash(git status)`: the tool's display name and its main argument."""
    args = call.arguments
    name = NAMES.get(call.name, call.name)
    if call.raw_arguments is not None:
        return f"{name}(<invalid arguments>)"
    main: Any = None
    for key in ("path", "command", "pattern", "call_id", "job_id", "prompt", "names", "name"):
        if key in args:
            main = args[key]
            break
    if call.name == "grep":
        where = args.get("path", ".")
        main = f'pattern: "{args.get("pattern", "")}", path: "{where}"'
    elif main is None:
        main = ", ".join(f"{k}={v}" for k, v in args.items())
    return f"{name}({_short(str(main), 90)})"


def edit_lines(call: ToolCall) -> list[tuple[str, str]]:
    """The edit as (sign, line) pairs: unchanged context, removals and additions."""
    args = call.arguments
    if call.name == "write" and isinstance(args.get("content"), str):
        return [("+", line) for line in args["content"].splitlines()]
    old, new = args.get("old_string"), args.get("new_string")
    if call.name != "edit" or not isinstance(old, str) or not isinstance(new, str):
        return []
    out: list[tuple[str, str]] = []
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), n=2, lineterm="")
    for line in list(diff)[2:]:
        if line.startswith("@@"):
            continue
        out.append((line[:1] if line[:1] in "+-" else " ", line[1:]))
    return out


def diff_block(lines: list[tuple[str, str]], limit: int = 40) -> Text:
    text = Text()
    for sign, line in lines[:limit]:
        style = REMOVED if sign == "-" else ADDED if sign == "+" else "dim"
        text.append(f"{sign} {line}", style=style)
        text.append("\n")
    if len(lines) > limit:
        text.append(f"… +{len(lines) - limit} lines\n", style="dim")
    text.rstrip()
    return text


def _counts(lines: list[tuple[str, str]]) -> str:
    added = sum(sign == "+" for sign, _ in lines)
    removed = sum(sign == "-" for sign, _ in lines)
    parts = [f"{added} addition{'s' * (added != 1)}"] if added else []
    if removed:
        parts.append(f"{removed} removal{'s' * (removed != 1)}")
    return " and ".join(parts) or "no changes"


def marked(marker: str, style: str, body: RenderableType) -> Table:
    """A body with a marker in its own column, so wrapped lines hang under the text."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1, no_wrap=True)
    grid.add_column()
    grid.add_row(Text(marker, style=style), body)
    return grid


def under(*lines: RenderableType) -> Table:
    """Lines hanging off the elbow under a tool call or a note."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=3, no_wrap=True)
    grid.add_column()
    for index, line in enumerate(lines):
        grid.add_row(Text(f"  {ELBOW}" if index == 0 else "", style="dim"), line)
    return grid


def _first_sentence(text: str) -> str:
    head = text.split("\n", 1)[0]
    cut = head.find(". ")
    return head[: cut + 1] if cut != -1 else head


def _preview(content: str, style: str) -> list[RenderableType]:
    lines = content.splitlines() or [""]
    shown: list[RenderableType] = [Text(line, style=style) for line in lines[:PREVIEW]]
    if len(lines) > PREVIEW:
        shown.append(Text(f"… +{len(lines) - PREVIEW} lines", style="dim"))
    return shown


def summarize(call: ToolCall, result: ToolResult) -> list[RenderableType]:
    """What a finished call shows under its label."""
    if result.status == "denied":
        reason = result.content.removeprefix("denied: ")
        return [Text(f"Denied: {_first_sentence(reason)}", style="red")]
    if result.status in ("error", "unknown"):
        return _preview(result.content, "red")
    if result.status == "interrupted":
        return [Text("Interrupted", style="red")]
    lines = result.content.splitlines()
    if call.name == "read":
        return [Text(f"Read {len(lines)} line{'s' * (len(lines) != 1)}")]
    if call.name in ("edit", "write"):
        changes = edit_lines(call)
        path = call.arguments.get("path", "")
        verb = "Updated" if call.name == "edit" else "Wrote"
        return [Text(f"{verb} {path} with {_counts(changes)}"), diff_block(changes)]
    if call.name in ("grep", "glob"):
        noun = "line" if call.name == "grep" else "file"
        found = [line for line in lines if line.strip()]
        return [Text(f"Found {len(found)} {noun}{'s' * (len(found) != 1)}")]
    return _preview(result.content, "dim")


@dataclass
class LiveState:
    """What the live region draws: the answer streaming now and the calls not yet finished."""

    streaming: str = ""
    kind: str | None = None
    calls: dict[str, ToolCall] = field(default_factory=dict[str, ToolCall])
    running: set[str] = field(default_factory=set[str])
    side: bool = False
    requesting: bool = False
    streamed_chars: int = 0
    usage: Usage = field(default_factory=Usage)
    context_tokens: int = 0
    run_started: float = field(default_factory=time.monotonic)


class Printer:
    """Prints finished events into scrollback through `write`, and keeps `live` current."""

    def __init__(self, write: Callable[[str], None], width: Callable[[], int], color: bool = True):
        self._write = write
        self._width = width
        self._color = color
        self.live = LiveState()

    def render(self, renderable: RenderableType) -> str:
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            width=max(self._width(), 20),
            force_terminal=self._color,
            color_system="truecolor" if self._color else None,
            no_color=not self._color,
            highlight=False,
            legacy_windows=False,
        )
        console.print(renderable)
        return buffer.getvalue()

    def print(self, renderable: RenderableType, gap: bool = True) -> None:
        text = self.render(Group(Text(""), renderable) if gap else renderable)
        self._write(text)

    def on_stream(self, event: StreamEvent) -> None:
        live = self.live
        if live.side:
            return
        if isinstance(event, ItemStarted):
            live.kind = event.kind if event.kind in ("message", "reasoning") else live.kind
            if event.kind == "message" and live.streaming:
                live.streaming = ""
        elif isinstance(event, ItemDelta) and event.text:
            live.streamed_chars += len(event.text)
            if live.kind == "message":
                live.streaming += event.text

    def on_event(self, event: Event) -> None:
        body = event.body
        live = self.live
        if isinstance(body, ModelRequestStarted):
            live.side = body.view in SIDE_VIEWS
            live.requesting = True
            live.streaming, live.kind = "", None
            live.context_tokens = body.token_estimate if not live.side else live.context_tokens
        elif isinstance(body, ModelRequestFinished):
            live.requesting = False
            live.usage = live.usage + body.usage
            live.streaming, live.kind = "", None
            live.side = False
        elif isinstance(body, ItemEvent):
            self._item(body.item)
        elif isinstance(body, ToolCallStarted):
            live.running.add(body.call_id)
        elif isinstance(body, UserMessage):
            live.run_started = time.monotonic()
            live.streamed_chars = 0
            self.print(Text(f"> {body.text}", style="bold"))
        elif isinstance(body, SteerMessage):
            self.print(Text(f"> {body.text}", style="bold"))
        else:
            self._notice(body)

    def _item(self, item: object) -> None:
        live = self.live
        if isinstance(item, Message) and item.role == "assistant":
            live.streaming = ""
            if item.text.strip():
                self.print(marked(DOT, "white", Markdown(item.text)))
        elif isinstance(item, Reasoning) and item.summary_text:
            thought = "\n".join(item.summary_text.strip().splitlines()[:PREVIEW])
            self.print(marked("✻", "dim", Text(thought, style="dim italic")))
        elif isinstance(item, ToolCall):
            live.calls[item.id] = item
            if item.name == EXIT_PLAN:
                plan = item.arguments.get("plan")
                panel = Panel(
                    Markdown(plan if isinstance(plan, str) else ""),
                    title="Plan", title_align="left", border_style="cyan", padding=(0, 1),
                )
                self.print(panel)
        elif isinstance(item, ToolResult):
            self._result(item)

    def _result(self, result: ToolResult) -> None:
        live = self.live
        call = live.calls.pop(result.call_id, None)
        live.running.discard(result.call_id)
        if call is None:
            return
        if call.name == EXIT_PLAN:
            if result.status == "ok":
                return
            reason = result.content.removeprefix("denied: ").removeprefix("plan not approved: ")
            reason = reason.split(". You are still in plan mode")[0]
            line = f"Kept planning: {reason}" if result.status == "denied" else result.content
            self.print(under(Text(line, style="red")), gap=False)
            return
        style = "green" if result.status == "ok" else "red"
        label = Text(call_label(call), style="bold")
        self.print(Group(marked(DOT, style, label), under(*summarize(call, result))))

    def _notice(self, body: object) -> None:
        if isinstance(body, ModeChanged):
            if body.source == "exit_plan":
                text = f"User approved the plan · mode: {body.mode}"
                self.print(under(Text(text, style="green")), gap=False)
            else:
                self.print(Text(MODE_NOTES.get(body.mode, body.mode), style="cyan"))
        elif isinstance(body, ProviderRetry):
            note = f"API retry {body.attempt} in {body.delay_s:.0f}s: {_short(body.reason, 80)}"
            self.print(under(Text(note, style="yellow")), gap=False)
        elif isinstance(body, ErrorEvent):
            self.print(marked(DOT, "red", Text(f"Error ({body.kind}): {body.message}", "red")))
        elif isinstance(body, Interrupted):
            self.print(under(Text("Interrupted by user", style="red")), gap=False)
        elif isinstance(body, BudgetExceeded):
            text = f"Stopped: the {body.limit.replace('_', ' ')} limit was reached ({body.value:g})"
            self.print(marked(DOT, "yellow", Text(text, style="yellow")))
        elif isinstance(body, CheckpointRestored):
            text = f"Undo: restored {', '.join(body.paths)}"
            self.print(marked(DOT, "green", Text(text)))
        elif isinstance(body, CompactionApplied):
            text = f"Compacted context: {body.tokens_before} → {body.tokens_after} tokens"
            self.print(marked("✻", "dim", Text(text, style="dim")))
        elif isinstance(body, HookBlocked) and body.point == "stop":
            self.print(marked(DOT, "yellow", Text(f"{body.hook}: {body.message}", "yellow")))
        elif isinstance(body, Repaired):
            text = f"Repaired call {body.call_id[:8]}: {body.status}, {body.reason}"
            self.print(under(Text(text, style="dim")), gap=False)


def banner(session: str, model: str, cwd: str, mode: str, resumed: bool) -> RenderableType:
    title = Text.assemble(("✻ ", "cyan"), ("CookCode", "bold"))
    rows = Text.assemble(
        (f"session {session}{' (resumed)' if resumed else ''}\n", "dim"),
        (f"model   {model}\n", "dim"),
        (f"cwd     {cwd}\n", "dim"),
        (f"mode    {mode}", "dim"),
    )
    return Padding(Panel(Group(title, Text(""), rows), border_style="cyan", expand=False), (0, 0))
