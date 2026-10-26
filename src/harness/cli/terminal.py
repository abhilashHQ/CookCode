import asyncio
import difflib
import json
import os
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any, TextIO

from harness.agent.approval import Decision, Risk
from harness.core.cancel import CancelToken
from harness.core.events import (
    BudgetExceeded,
    CheckpointRestored,
    ErrorEvent,
    Event,
    EventBody,
    Interrupted,
    ItemEvent,
    ProviderRetry,
    Repaired,
    SessionFinished,
)
from harness.core.items import ToolCall, ToolResult
from harness.providers.base import (
    ItemDelta,
    ItemStarted,
    ModelCaps,
    ModelRequest,
    Provider,
    StreamEvent,
)
from harness.store.base import SessionLog

DIM, RED, GREEN, CYAN, RESET = "\x1b[2m", "\x1b[31m", "\x1b[32m", "\x1b[36m", "\x1b[0m"
RESULT_PREVIEW_LINES = 6


class Terminal:
    """Line input that an interrupt can cancel, plus colored output when stdout is a TTY."""

    def __init__(self, out: TextIO = sys.stdout, stdin: TextIO = sys.stdin) -> None:
        self._out = out
        self._stdin = stdin
        self._color = out.isatty()
        self._reader: asyncio.StreamReader | None = None
        self._was_blocking: bool | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        try:
            fd = self._stdin.fileno()
            self._was_blocking = os.get_blocking(fd)
            await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), self._stdin)
        except (OSError, ValueError):
            self._was_blocking = None
            return
        self._reader = reader

    def close(self) -> None:
        if self._was_blocking is not None:
            os.set_blocking(self._stdin.fileno(), self._was_blocking)

    def paint(self, text: str, color: str) -> str:
        return f"{color}{text}{RESET}" if self._color else text

    def write(self, text: str, color: str | None = None) -> None:
        self._out.write(self.paint(text, color) if color else text)
        self._out.flush()

    def line(self, text: str = "", color: str | None = None) -> None:
        self.write(text + "\n", color)

    async def readline(self, prompt: str) -> str | None:
        self.write(prompt)
        if self._reader is None:
            line = await asyncio.to_thread(self._stdin.readline)
        else:
            line = (await self._reader.readline()).decode("utf-8", "replace")
        return line.rstrip("\n") if line else None


def _short(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def describe_call(call: ToolCall) -> str:
    if call.raw_arguments is not None:
        return f"{call.name}(<invalid JSON: {_short(call.raw_arguments)}>)"
    if call.name == "bash" and isinstance(call.arguments.get("command"), str):
        return f"bash $ {_short(call.arguments['command'], 160)}"
    shown = ", ".join(
        f"{key}={_short(value)}"
        for key, value in call.arguments.items()
        if key not in ("content", "old_string", "new_string")
    )
    return f"{call.name}({shown})"


def call_diff(call: ToolCall) -> list[str]:
    arguments = call.arguments
    if call.name == "edit":
        old, new = arguments.get("old_string"), arguments.get("new_string")
        if isinstance(old, str) and isinstance(new, str):
            lines = difflib.unified_diff(
                old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=2
            )
            return list(lines)[2:]
    if call.name == "write":
        content = arguments.get("content")
        if isinstance(content, str):
            count = len(content.splitlines())
            head = [f"+{line}" for line in content.splitlines()[:RESULT_PREVIEW_LINES]]
            return [*head, *([f"... {count - len(head)} more lines"] if count > len(head) else [])]
    return []


class Display:
    """Renders provider stream events and log events for the plain CLI."""

    def __init__(self, terminal: Terminal) -> None:
        self._t = terminal
        self._kinds: dict[int, str] = {}
        self._calls: dict[str, ToolCall] = {}
        self._midline = False

    def _newline(self) -> None:
        if self._midline:
            self._t.line()
            self._midline = False

    def on_stream(self, event: StreamEvent) -> None:
        if isinstance(event, ItemStarted):
            if event.index == 0:
                self._kinds = {}
            self._kinds[event.index] = event.kind
            self._newline()
        elif isinstance(event, ItemDelta) and event.text:
            reasoning = self._kinds.get(event.index) == "reasoning"
            self._t.write(event.text, DIM if reasoning else None)
            self._midline = not event.text.endswith("\n")

    def on_event(self, event: Event) -> None:
        self.render(event.body)

    def render(self, body: EventBody) -> None:
        t = self._t
        if isinstance(body, ItemEvent) and isinstance(body.item, ToolCall):
            self._newline()
            call = body.item
            self._calls[call.id] = call
            t.line(f"> {describe_call(call)}", CYAN)
            for line in call_diff(call):
                color = GREEN if line.startswith("+") else RED if line.startswith("-") else DIM
                t.line(f"  {line}", color)
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolResult):
            self._render_result(body.item)
        elif isinstance(body, ItemEvent):
            self._newline()
        elif isinstance(body, ProviderRetry):
            self._newline()
            t.line(
                f"[retry {body.attempt}] {_short(body.reason, 160)}; waiting {body.delay_s:.1f}s",
                DIM,
            )
        elif isinstance(body, ErrorEvent):
            self._newline()
            t.line(f"[error] {body.kind}: {body.message}", RED)
        elif isinstance(body, Repaired):
            t.line(f"[repaired] call {body.call_id[:8]}: {body.status}, {body.reason}", DIM)
        elif isinstance(body, Interrupted):
            self._newline()
            t.line(f"[interrupted during {body.phase}]", RED)
        elif isinstance(body, BudgetExceeded):
            self._newline()
            t.line(f"[budget] {body.limit} limit reached at {body.value:g}", RED)
        elif isinstance(body, CheckpointRestored):
            t.line(f"[undo] restored {', '.join(body.paths)}", GREEN)
        elif isinstance(body, SessionFinished):
            self._newline()
            usage = body.usage
            t.line(
                f"[{body.status}] {body.turns} turns, {usage.total_input} in / "
                f"{usage.output} out tokens",
                DIM,
            )

    def _render_result(self, result: ToolResult) -> None:
        lines = result.content.splitlines()
        color = GREEN if result.status == "ok" else RED
        label = "ok" if result.status == "ok" else result.status
        self._t.line(f"  [{label}] {len(result.content)} chars", color)
        preview = lines if result.status != "ok" else lines[:RESULT_PREVIEW_LINES]
        for line in preview[: RESULT_PREVIEW_LINES * 2]:
            self._t.line(f"  | {_short(line, 160)}", DIM)
        if len(lines) > len(preview):
            self._t.line(f"  | ... {len(lines) - len(preview)} more lines", DIM)


class _Observer[T]:
    """Calls a client sink, and turns it off after its first failure so it cannot break the run."""

    def __init__(self, sink: Callable[[T], None]) -> None:
        self._sink: Callable[[T], None] | None = sink
        self.error: Exception | None = None

    def __call__(self, value: T) -> None:
        if self._sink is None:
            return
        try:
            self._sink(value)
        except Exception as exc:
            self._sink, self.error = None, exc


class DisplayProvider:
    """Wraps a provider so a client sees stream events while the loop consumes them."""

    def __init__(self, inner: Provider, sink: Callable[[StreamEvent], None]) -> None:
        self._inner = inner
        self._sink = _Observer(sink)
        self.name = inner.name

    def capabilities(self, model: str) -> ModelCaps:
        return self._inner.capabilities(model)

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncIterator[StreamEvent]:
        inner = self._inner.stream(request, cancel)
        try:
            async for event in inner:
                self._sink(event)
                yield event
        finally:
            if isinstance(inner, AsyncGenerator):
                await inner.aclose()


class ObservedLog:
    """Wraps a session log so a client sees every event the core appends."""

    def __init__(self, inner: SessionLog, sink: Callable[[Event], None]) -> None:
        self._inner = inner
        self._sink = _Observer(sink)

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    @property
    def next_seq(self) -> int:
        return self._inner.next_seq

    def append(self, body: EventBody, *, durable: bool = False) -> Event:
        event = self._inner.append(body, durable=durable)
        self._sink(event)
        return event

    def read(self, from_seq: int = 0) -> list[Event]:
        return self._inner.read(from_seq)

    def close(self) -> None:
        self._inner.close()


def always_rule(call: ToolCall, risk: Risk) -> str | None:
    if call.name == "bash":
        command = call.arguments.get("command")
        return f"bash:{command}" if isinstance(command, str) else None
    if len(risk.subjects) == 1:
        return f"{call.name}:{risk.subjects[0]}"
    return None


class TerminalApprover:
    def __init__(self, terminal: Terminal) -> None:
        self._t = terminal

    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        t = self._t
        t.line(f"? {risk.side_effect} call needs approval: {risk.reason}", CYAN)
        if risk.subjects:
            t.line(f"  touches {', '.join(risk.subjects)} (cwd {risk.cwd})", DIM)
        rule = always_rule(call, risk)
        choices = "[y]es, [n]o" + (f", [a]lways ({rule})" if rule else "")
        while True:
            answer = await t.readline(f"  allow? {choices}: ")
            if answer is None:
                return Decision(action="deny", reason="no answer: input closed")
            answer = answer.strip().lower()
            if answer in ("y", "yes"):
                return Decision(action="allow")
            if answer in ("a", "always") and rule is not None:
                return Decision(action="allow_always", rule=rule)
            if answer in ("n", "no"):
                reason = await t.readline("  reason for the model (optional): ")
                return Decision(action="deny", reason=(reason or "").strip() or "the user said no")
