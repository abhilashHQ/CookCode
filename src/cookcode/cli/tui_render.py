"""The TUI's own renderer (decision 0052): log events and stream events become widgets, one per
thing the user reads. It observes the same two interfaces as the plain CLI's Display (0026), so
the core does not know it exists. Every widget keeps its plain text in `plain` for tests."""

import json
from typing import Any, ClassVar

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

from cookcode.agent.context import SIDE_VIEWS
from cookcode.agent.tools import EXIT_PLAN
from cookcode.cli.terminal import call_diff, describe_call
from cookcode.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    BudgetExceeded,
    CheckpointRestored,
    CompactionApplied,
    ErrorEvent,
    Event,
    HookBlocked,
    Interrupted,
    ItemEvent,
    ModeChanged,
    ModelRequestStarted,
    ProviderRetry,
    Repaired,
    SessionFinished,
    SteerMessage,
    ToolCallFinished,
    ToolCallStarted,
    UserMessage,
)
from cookcode.core.items import Message, Reasoning, ToolCall, ToolResult
from cookcode.providers.base import ItemDelta, ItemStarted, StreamEvent

PREVIEW_LINES = 20


def diff_text(lines: list[str]) -> Text:
    text = Text()
    for line in lines:
        style = "green" if line.startswith("+") else "red" if line.startswith("-") else "dim"
        text.append(line + "\n", style=style)
    return text


class Note(Static):
    """One line or paragraph: the user's prompt, a steer, a retry, an error, a mode change."""

    def __init__(self, plain: str, kind: str) -> None:
        super().__init__(Text(plain), classes=f"note {kind}")
        self.plain = plain


class AssistantText(Static):
    """Assistant prose, rendered as Markdown and grown delta by delta while it streams."""

    def __init__(self, text: str = "") -> None:
        super().__init__(classes="assistant")
        self.plain = text
        self.done = False
        self._show()

    def feed(self, delta: str) -> None:
        self.plain += delta
        self._show()

    def finish(self, text: str) -> None:
        self.plain, self.done = text, True
        self._show()

    def _show(self) -> None:
        self.update(Markdown(self.plain) if self.plain else Text(""))


class Thinking(Collapsible):
    """Reasoning text, collapsed: there when wanted, out of the way otherwise."""

    def __init__(self, text: str = "") -> None:
        self._body = Static(Text(text, style="dim italic"))
        super().__init__(self._body, title="thinking", collapsed=True, classes="thinking")
        self.plain = text
        self.done = False

    def feed(self, delta: str) -> None:
        self.plain += delta
        self._body.update(Text(self.plain, style="dim italic"))

    def finish(self, text: str | None) -> None:
        self.done = True
        if text:
            self.plain = text
            self._body.update(Text(text, style="dim italic"))


class ToolBlock(Collapsible):
    """One tool call: its title carries the call and its state, the body its diff and result.
    Calls that change files open with their diff; failures open with their output."""

    STATES: ClassVar[dict[str, str]] = {
        "pending": "", "asking": "awaiting approval", "running": "running",
    }

    def __init__(self, call: ToolCall) -> None:
        self.call = call
        self.status = "pending"
        self.detail = ""
        diff = call_diff(call)
        self._diff = Static(diff_text(diff), classes="diff")
        self._diff.display = bool(diff)
        self._output = Static(classes="output")
        self._output.display = False
        super().__init__(
            self._diff, self._output, title=describe_call(call), collapsed=not diff,
            classes="tool",
        )
        self.plain = describe_call(call)

    def mark(self, status: str, detail: str = "") -> None:
        self.status = status
        if detail:
            self.detail = detail
        label = self.STATES.get(status, status)
        parts = [describe_call(self.call), label, self.detail]
        self.title = "  ".join(part for part in parts if part)
        self.set_class(status in ("error", "denied", "unknown", "interrupted"), "failed")
        self.set_class(status == "ok", "ok")

    def result(self, result: ToolResult) -> None:
        lines = result.content.splitlines()
        shown = lines[:PREVIEW_LINES]
        text = Text("\n".join(shown), style="dim" if result.status == "ok" else "red")
        if len(lines) > len(shown):
            text.append(f"\n... {len(lines) - len(shown)} more lines", style="dim italic")
        self._output.update(text)
        self._output.display = True
        self.plain += "\n" + result.content
        self.mark(result.status, f"{len(lines)} lines" if result.status == "ok" else "")
        if result.status != "ok":
            self.collapsed = False


class PlanBlock(Static):
    """The plan the agent proposed with exit_plan, and what the user decided."""

    def __init__(self, call: ToolCall) -> None:
        plan = call.arguments.get("plan")
        self.plan = plan if isinstance(plan, str) else json.dumps(call.arguments)
        super().__init__(classes="plan")
        self.call = call
        self.plain = self.plan
        self.border_title = "proposed plan"
        self._show()

    def decided(self, approved: bool, detail: str) -> None:
        self.border_subtitle = detail
        self.set_class(approved, "approved")
        self.set_class(not approved, "rejected")

    def _show(self) -> None:
        self.update(Markdown(self.plan))


class Transcript(VerticalScroll):
    """Turns the runtime's stream and log into widgets, and follows the bottom unless the
    user has scrolled up to read."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._streaming: list[AssistantText | Thinking] = []
        self._tools: dict[str, ToolBlock] = {}
        self._plans: dict[str, PlanBlock] = {}
        self._side = False

    @property
    def plain(self) -> str:
        return "\n".join(
            str(getattr(child, "plain", "")) for child in self.children
        )

    def add(self, widget: Static | Collapsible) -> None:
        following = self.max_scroll_y - self.scroll_y <= 2
        self.mount(widget)
        if following:
            self.call_after_refresh(self.scroll_end, animate=False)

    def note(self, text: str, kind: str = "info") -> None:
        self.add(Note(text, kind))

    def on_stream(self, event: StreamEvent) -> None:
        if self._side:
            return
        if isinstance(event, ItemStarted):
            if event.kind == "message":
                block: AssistantText | Thinking = AssistantText()
            elif event.kind == "reasoning":
                block = Thinking()
            else:
                return
            self._streaming.append(block)
            self.add(block)
        elif isinstance(event, ItemDelta) and event.text:
            open_blocks = [b for b in self._streaming if not b.done]
            if open_blocks:
                open_blocks[-1].feed(event.text)
                self._follow()

    def _follow(self) -> None:
        if self.max_scroll_y - self.scroll_y <= 4:
            self.scroll_end(animate=False)

    def _next_open[B: AssistantText | Thinking](self, kind: type[B]) -> B | None:
        return next(
            (b for b in self._streaming if isinstance(b, kind) and not b.done), None
        )

    def on_log_event(self, event: Event) -> None:
        body = event.body
        if isinstance(body, ModelRequestStarted):
            self._side = body.view in SIDE_VIEWS
            self._streaming = [b for b in self._streaming if not b.done]
            if self._side:
                self.note(f"[{body.view}] side call to the model", "dim")
        elif isinstance(body, ItemEvent):
            self._item(body)
        elif isinstance(body, UserMessage):
            self.note(f"> {body.text}", "user")
        elif isinstance(body, SteerMessage):
            self.note(f"[steer] {body.text}", "steer")
        elif isinstance(body, ApprovalRequested) and body.call_id in self._tools:
            self._tools[body.call_id].mark("asking")
        elif isinstance(body, ApprovalResolved):
            self._approval(body)
        elif isinstance(body, ToolCallStarted) and body.call_id in self._tools:
            self._tools[body.call_id].mark("running")
        elif isinstance(body, ToolCallFinished) and body.call_id in self._tools:
            self._tools[body.call_id].detail = f"{body.duration_s:.1f}s"
        else:
            self._notice(body)

    def _item(self, body: ItemEvent) -> None:
        item = body.item
        if isinstance(item, Message) and item.role == "assistant":
            streamed = self._next_open(AssistantText)
            if streamed is not None:
                streamed.finish(item.text)
            elif item.text:
                block = AssistantText(item.text)
                block.done = True
                self.add(block)
        elif isinstance(item, Reasoning):
            thinking = self._next_open(Thinking)
            if thinking is not None:
                thinking.finish(item.summary_text)
            elif item.summary_text:
                block = Thinking(item.summary_text)
                block.done = True
                self.add(block)
        elif isinstance(item, ToolCall) and item.name == EXIT_PLAN:
            plan = PlanBlock(item)
            self._plans[item.id] = plan
            self.add(plan)
        elif isinstance(item, ToolCall):
            tool = ToolBlock(item)
            self._tools[item.id] = tool
            self.add(tool)
        elif isinstance(item, ToolResult):
            if item.call_id in self._tools:
                self._tools[item.call_id].result(item)
            elif item.call_id in self._plans and item.status != "ok":
                self.note(item.content, "error" if item.status == "error" else "dim")

    def _approval(self, body: ApprovalResolved) -> None:
        plan = self._plans.get(body.call_id)
        if plan is not None:
            approved = body.decision != "deny"
            detail = body.reason or ("approved" if approved else "rejected")
            plan.decided(approved, detail)
        elif body.decision != "deny" and body.rule and body.call_id in self._tools:
            self._tools[body.call_id].detail = f"allowed by {body.rule}"

    def _notice(self, body: object) -> None:
        if isinstance(body, ProviderRetry):
            self.note(f"[retry {body.attempt}] {body.reason}; waiting {body.delay_s:.1f}s", "dim")
        elif isinstance(body, ErrorEvent):
            self.note(f"[error] {body.kind}: {body.message}", "error")
        elif isinstance(body, Repaired):
            self.note(f"[repaired] call {body.call_id[:8]}: {body.status}, {body.reason}", "dim")
        elif isinstance(body, Interrupted):
            self.note(f"[interrupted during {body.phase}]", "error")
        elif isinstance(body, BudgetExceeded):
            self.note(f"[budget] {body.limit} limit reached at {body.value:g}", "error")
        elif isinstance(body, CheckpointRestored):
            self.note(f"[undo] restored {', '.join(body.paths)}", "ok")
        elif isinstance(body, CompactionApplied):
            self.note(
                f"[context] {body.strategy}: {body.tokens_before} -> {body.tokens_after} tokens",
                "dim",
            )
        elif isinstance(body, HookBlocked):
            self.note(f"[{body.hook}] {body.message}", "steer")
        elif isinstance(body, ModeChanged):
            self.note(f"[mode] {body.previous or 'start'} -> {body.mode}", "mode")
        elif isinstance(body, SessionFinished):
            usage = body.usage
            self.note(
                f"[{body.status}] {body.turns} turns, {usage.total_input} in / "
                f"{usage.output} out tokens",
                "dim",
            )


def render_side(rows: list[tuple[str, RenderableType]]) -> Group:
    out: list[RenderableType] = []
    for label, value in rows:
        out.append(Text(label.upper(), style="bold dim"))
        out.append(value)
        out.append(Text(""))
    return Group(*out)
