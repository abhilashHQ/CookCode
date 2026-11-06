"""The interactive client (decision 0053), in the terminal's own scrollback. `Printer` prints
finished output above; below it, prompt_toolkit keeps a small live region: the answer while it
streams, calls in flight, a spinner, the input box and the mode line. Approvals and the plan
review are numbered menus in that region. Typing while the agent works steers it at the next
step; Esc or Ctrl-C stops a run, a second Ctrl-C forces it, and Ctrl-C at an empty prompt
quits. Shift-Tab cycles the mode."""

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import (
    ConditionalContainer,
    Dimension,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.output import Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from cookcode.agent.approval import Decision, Risk
from cookcode.agent.gate import MODES, Mode, Policy
from cookcode.agent.loop import Agent
from cookcode.agent.tools import EXIT_PLAN
from cookcode.agent.undo import undo
from cookcode.cli.app import EXIT_CODES, Runtime
from cookcode.cli.render import DOT, Printer, banner, call_label, diff_block, edit_lines, marked
from cookcode.cli.terminal import always_rule
from cookcode.core.cancel import CancelToken
from cookcode.core.events import Event, ModeChanged, SteerMessage
from cookcode.core.items import ToolCall
from cookcode.providers.base import Provider, StreamEvent

CYCLE: tuple[Mode, ...] = ("ask", "auto-edit", "plan")
SPINNER = "·✢✶✻✽✻✶✢"
HELP = (
    "/mode <plan|ask|auto-edit|yolo> sets the mode, /undo [n] reverts the file edits of the "
    "last n turns, /exit quits. Shift-Tab cycles the mode; Esc stops a run; Alt-Enter adds a "
    "line."
)
MODE_LINE: dict[str, tuple[str, str]] = {
    "plan": ("‖ plan mode on", "class:mode.plan"),
    "auto-edit": ("» accept edits on", "class:mode.edit"),
    "yolo": ("»» yolo: everything runs", "class:mode.yolo"),
    "ask": ("ask before edits", "class:dim"),
}
STYLE = Style.from_dict({
    "frame": "#6c6c6c",
    "dim": "#8a8a8a",
    "placeholder": "#6c6c6c italic",
    "prompt": "bold",
    "mode.plan": "#5fafd7 bold",
    "mode.edit": "#af87ff bold",
    "mode.yolo": "#ff5f5f bold",
    "context.full": "#ffaf00 bold",
})
CONTEXT_WARNING = 0.8
FEEDBACK = "feedback"
Choice = Decision | Literal["feedback"]
MenuSpec = tuple[RenderableType | None, str, list[tuple[str, Choice]]]


@dataclass
class Menu:
    body: RenderableType | None
    question: str
    options: list[tuple[str, Choice]]
    future: asyncio.Future[Choice]
    selected: int = 0


def _tokens(count: int) -> str:
    if count < 1000:
        return str(count)
    return f"{count / 1000:.1f}k" if count < 10_000 else f"{round(count / 1000)}k"


def context_meter(used: int, window: int | None) -> tuple[str, str]:
    """The estimated context against the model's window, warning once it is 80% full."""
    if window is None:
        return "class:dim", f"ctx ~{used}"
    style = "class:context.full" if used >= CONTEXT_WARNING * window else "class:dim"
    return style, f"ctx ~{_tokens(used)} / {_tokens(window)}"


def rounded(body: Window, style: str = "class:frame") -> HSplit:
    def edge(left: str, right: str) -> VSplit:
        return VSplit([
            Window(width=1, height=1, char=left, style=style),
            Window(height=1, char="─", style=style),
            Window(width=1, height=1, char=right, style=style),
        ], height=1)

    side = Window(width=1, char="│", style=style)
    return HSplit([edge("╭", "╮"), VSplit([side, body, side]), edge("╰", "╯")])


class InlineApprover:
    def __init__(self, app: "InlineApp") -> None:
        self._app = app

    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        app = self._app
        choice = await app.ask(self._menu(call, risk))
        if isinstance(choice, Decision):
            return choice
        reason = await app.ask_feedback(
            "what should change in the plan?" if call.name == EXIT_PLAN
            else "tell the agent what to do differently"
        )
        return Decision(action="deny", reason=reason or "the user said no")

    def _menu(self, call: ToolCall, risk: Risk) -> MenuSpec:
        if call.name == EXIT_PLAN:
            return None, "Would you like to proceed?", [
                ("Yes, and auto-accept edits", Decision(action="allow_always")),
                ("Yes, and manually approve edits", Decision(action="allow")),
                ("No, keep planning", FEEDBACK),
            ]
        changes = edit_lines(call)
        path = call.arguments.get("path", "")
        command = call.arguments.get("command")
        if call.name in ("edit", "write"):
            title = "Edit file" if call.name == "edit" else "Write file"
            detail: RenderableType = Group(Text(str(path), style="bold"), diff_block(changes, 20))
            verb = "make this edit to" if call.name == "edit" else "create"
            question = f"Do you want to {verb} {path}?"
        elif call.name == "bash" and isinstance(command, str):
            title, detail = "Bash command", Text(command, style="bold")
            question = "Do you want to run this command?"
        else:
            title, detail = "Tool call", Text(call_label(call), style="bold")
            question = "Do you want to allow this call?"
        options: list[tuple[str, Choice]] = [("Yes", Decision(action="allow"))]
        rule = always_rule(call, risk)
        if rule is not None:
            options.append((f"Yes, and don't ask again for {rule}",
                            Decision(action="allow_always", rule=rule)))
        options.append(("No, and tell the agent what to do differently (esc)", FEEDBACK))
        body = Panel(
            Group(detail, Text(risk.reason, style="dim")),
            title=title, title_align="left", border_style="yellow", padding=(0, 1),
        )
        return body, question, options


class InlineApp:
    def __init__(
        self,
        args: argparse.Namespace,
        provider: Provider | None = None,
        *,
        input: Input | None = None,
        output: Output | None = None,
        write: Callable[[str], None] | None = None,
        policy: Policy | None = None,
    ) -> None:
        self.args = args
        self.provider = provider
        self.policy = policy
        self.status = "done"
        self._write = write
        self.printer = Printer(self._emit, self._columns)
        self.buffer = Buffer(multiline=True)
        self.menu: Menu | None = None
        self.feedback: asyncio.Future[str] | None = None
        self.feedback_label = ""
        self.started = False
        self.queued: list[str] = []
        self.mode: str = args.mode
        self.pending_mode: Mode | None = None
        self.agent: Agent | None = None
        self.runtime: Runtime | None = None
        self.token: CancelToken | None = None
        self.run_task: asyncio.Task[None] | None = None
        self.app: Application[int] = Application(
            layout=Layout(self._layout(), focused_element=None),
            key_bindings=self._bindings(),
            style=STYLE,
            full_screen=False,
            refresh_interval=0.12,
            erase_when_done=True,
            input=input,
            output=output,
        )
        self.app.layout.focus(self._input_window)
        self.app.ttimeoutlen = 0.05

    # -- output -------------------------------------------------------------------------------

    def _columns(self) -> int:
        return self.app.output.get_size().columns if hasattr(self, "app") else 80

    def _emit(self, text: str) -> None:
        if self._write is not None:
            self._write(text)
        else:
            sys.stdout.write(text)
            sys.stdout.flush()

    def _on_event(self, event: Event) -> None:
        self.printer.on_event(event)
        body = event.body
        if isinstance(body, SteerMessage) and body.text in self.queued:
            self.queued.remove(body.text)
        elif isinstance(body, ModeChanged):
            self.mode = body.mode
        self.app.invalidate()

    def _on_stream(self, event: StreamEvent) -> None:
        self.printer.on_stream(event)
        self.app.invalidate()

    # -- layout -------------------------------------------------------------------------------

    def _layout(self) -> HSplit:
        running = Condition(lambda: self.token is not None)
        in_menu = Condition(lambda: self.menu is not None)
        self._input_window = Window(
            BufferControl(self.buffer, input_processors=[BeforeInput(self._prefix)]),
            height=Dimension(min=1, max=10),
            wrap_lines=True,
            dont_extend_height=True,
        )
        live = Window(FormattedTextControl(self._live), dont_extend_height=True, wrap_lines=True)
        menu = Window(FormattedTextControl(self._menu), dont_extend_height=True, wrap_lines=True)
        status = VSplit([
            Window(FormattedTextControl(self._mode_line), height=1),
            Window(FormattedTextControl(self._usage_line), height=1, align=WindowAlign.RIGHT),
        ], height=1)
        return HSplit([
            ConditionalContainer(live, filter=running | Condition(lambda: bool(self.queued))),
            ConditionalContainer(menu, filter=in_menu),
            ConditionalContainer(
                HSplit([Window(height=1), rounded(self._input_window)]), filter=~in_menu
            ),
            status,
        ])

    def _placeholder(self) -> str:
        if self.feedback is not None:
            return f"{self.feedback_label} (enter to send, esc to skip)"
        if self.token is not None:
            return "type to steer the agent at its next step"
        return "" if self.started else "describe a task, or /help"

    def _prefix(self) -> StyleAndTextTuples:
        prompt: StyleAndTextTuples = [("class:prompt", " > ")]
        if self.buffer.text:
            return prompt
        return [*prompt, ("class:placeholder", self._placeholder())]

    def _ansi(self, renderable: RenderableType) -> StyleAndTextTuples:
        text = self.printer.render(renderable).rstrip("\n")
        return ANSI(text).__pt_formatted_text__()

    def _live(self) -> StyleAndTextTuples:
        live = self.printer.live
        parts: list[RenderableType] = []
        if live.streaming.strip():
            lines = live.streaming.splitlines()[-16:]
            parts.append(marked(DOT, "white", Markdown("\n".join(lines))))
        for call_id, call in live.calls.items():
            if call_id in live.running:
                parts.append(marked(DOT, "yellow", Text.assemble(
                    (call_label(call), "bold"), ("  running…", "dim"),
                )))
        if self.token is not None:
            elapsed = int(time.monotonic() - live.run_started)
            tokens = live.usage.output + live.streamed_chars // 4
            verb = self._verb()
            frame = SPINNER[int(time.monotonic() * 8) % len(SPINNER)]
            parts.append(Text.assemble(
                (f"{frame} {verb}… ", "#d7875f"),
                (f"({elapsed}s · ↓ {tokens} tokens · esc to interrupt)", "dim"),
            ))
        for text in self.queued:
            parts.append(Text(f"> {text}  (queued for the next step)", style="dim"))
        if not parts:
            return []
        return self._ansi(Group(Text(""), *parts))

    def _verb(self) -> str:
        live = self.printer.live
        if self.menu is not None or self.feedback is not None:
            return "Waiting for you"
        if live.running:
            return "Running"
        if live.kind == "reasoning":
            return "Thinking"
        if live.side:
            return "Compacting"
        return "Working"

    def _menu(self) -> StyleAndTextTuples:
        menu = self.menu
        if menu is None:
            return []
        rows: list[RenderableType] = [Text(""), menu.body] if menu.body else [Text("")]
        rows.append(Text(menu.question, style="bold"))
        for index, (label, _) in enumerate(menu.options):
            chosen = index == menu.selected
            pointer = "\u276f" if chosen else " "
            rows.append(Text(f"{pointer} {index + 1}. {label}",
                             style="bold cyan" if chosen else ""))
        return self._ansi(Group(*rows))

    def _mode_line(self) -> StyleAndTextTuples:
        mode = self.pending_mode or self.mode
        label, style = MODE_LINE.get(mode, (mode, "class:dim"))
        later = " (next message)" if self.pending_mode is not None else ""
        return [(style, f"  {label}{later}"), ("class:dim", " (shift+tab to cycle)")]

    def _usage_line(self) -> StyleAndTextTuples:
        usage = self.printer.live.usage
        context = self.printer.live.context_tokens
        cached = f" · {usage.cache_read} cached" if usage.cache_read else ""
        return [
            ("class:dim", f"{self.args.model} · {usage.total_input} in{cached} · "
                          f"{usage.output} out · "),
            context_meter(context, self.args.context_window),
            ("class:dim", "  "),
        ]

    # -- keys ---------------------------------------------------------------------------------

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()
        in_menu = Condition(lambda: self.menu is not None)
        typing = ~in_menu

        @kb.add("enter", filter=typing)
        def _(event: KeyPressEvent) -> None:
            self._submit()

        @kb.add("escape", "enter", filter=typing)
        def _(event: KeyPressEvent) -> None:
            self.buffer.insert_text("\n")

        @kb.add("escape", filter=typing, eager=True)
        def _(event: KeyPressEvent) -> None:
            if self.feedback is not None:
                self._resolve_feedback("")
            elif self.token is not None:
                self.interrupt()

        @kb.add("c-c")
        def _(event: KeyPressEvent) -> None:
            if self.menu is not None:
                self._choose(len(self.menu.options) - 1)
            elif self.feedback is not None:
                self._resolve_feedback("")
            elif self.token is not None:
                self.interrupt()
            elif self.buffer.text:
                self.buffer.reset()
            else:
                self.app.exit(result=0)

        @kb.add("c-d", filter=typing)
        def _(event: KeyPressEvent) -> None:
            if not self.buffer.text and self.token is None:
                self.app.exit(result=0)

        @kb.add("s-tab")
        def _(event: KeyPressEvent) -> None:
            self.cycle_mode()

        @kb.add("up", filter=in_menu)
        def _(event: KeyPressEvent) -> None:
            self._move(-1)

        @kb.add("down", filter=in_menu)
        def _(event: KeyPressEvent) -> None:
            self._move(1)

        @kb.add("enter", filter=in_menu)
        def _(event: KeyPressEvent) -> None:
            if self.menu is not None:
                self._choose(self.menu.selected)

        @kb.add("escape", filter=in_menu, eager=True)
        def _(event: KeyPressEvent) -> None:
            if self.menu is not None:
                self._choose(len(self.menu.options) - 1)

        for digit in "123456789":
            @kb.add(digit, filter=in_menu)
            def _(event: KeyPressEvent, index: int = int(digit) - 1) -> None:
                if self.menu is not None and index < len(self.menu.options):
                    self._choose(index)

        return kb

    def _move(self, step: int) -> None:
        if self.menu is not None:
            self.menu.selected = (self.menu.selected + step) % len(self.menu.options)

    def _choose(self, index: int) -> None:
        menu = self.menu
        if menu is None:
            return
        self.menu = None
        if not menu.future.done():
            menu.future.set_result(menu.options[index][1])
        self.app.invalidate()

    def _resolve_feedback(self, text: str) -> None:
        future, self.feedback = self.feedback, None
        if future is not None and not future.done():
            future.set_result(text)
        self.app.invalidate()

    # -- actions ------------------------------------------------------------------------------

    async def ask(self, spec: MenuSpec) -> Choice:
        body, question, options = spec
        future: asyncio.Future[Choice] = asyncio.get_running_loop().create_future()
        menu = Menu(body, question, options, future)
        self.menu = menu
        self.app.invalidate()
        try:
            return await future
        finally:
            if self.menu is menu:
                self.menu = None
            self.app.invalidate()

    async def ask_feedback(self, label: str) -> str:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.feedback, self.feedback_label = future, label
        self.buffer.reset()
        self.app.invalidate()
        try:
            return await future
        finally:
            if self.feedback is future:
                self.feedback = None
            self.app.invalidate()

    def _submit(self) -> None:
        text = self.buffer.text.strip()
        self.buffer.reset()
        if self.feedback is not None:
            self._resolve_feedback(text)
            return
        if not text:
            return
        if self.token is not None and self.agent is not None:
            self.queued.append(text)
            self.agent.steer(text)
        elif text == "/exit":
            self.app.exit(result=0)
        elif text.startswith("/mode"):
            self.set_mode(text.removeprefix("/mode").strip())
        elif text.startswith("/undo"):
            self.app.create_background_task(self._undo(text.removeprefix("/undo").strip()))
        elif text.startswith("/"):
            self.printer.print(Text(HELP, style="dim"))
        else:
            self.start(text)

    def start(self, prompt: str) -> None:
        if self.agent is None or self.token is not None:
            return
        self.started = True
        self.token = CancelToken()
        self.printer.live.run_started = time.monotonic()
        self.run_task = asyncio.get_running_loop().create_task(self._run(prompt, self.token))

    async def _run(self, prompt: str, token: CancelToken) -> None:
        assert self.agent is not None
        try:
            self.status = (await self.agent.run(prompt, cancel=token)).status
        except asyncio.CancelledError:
            self.status = "interrupted"
            self.printer.print(Text("forced stop: the next run repairs the log", style="red"))
        except Exception as exc:
            self.status = "error"
            self.printer.print(Text(f"run failed: {type(exc).__name__}: {exc}", style="red"))
        finally:
            self.token = None
            self.run_task = None
            self.queued.clear()
            if self.pending_mode is not None:
                self.agent.set_mode(self.pending_mode)
                self.mode, self.pending_mode = self.pending_mode, None
            self.app.invalidate()
            if self.args.once:
                self.app.exit(result=0)

    def interrupt(self) -> None:
        if self.token is not None and not self.token.cancelled:
            self.token.cancel("user")
        elif self.run_task is not None:
            self.run_task.cancel()

    def set_mode(self, name: str) -> None:
        modes: dict[str, Mode] = {m: m for m in MODES}
        mode = modes.get(name)
        if mode is None or self.agent is None:
            self.printer.print(Text(f"pick a mode: {', '.join(MODES)}", style="red"))
            return
        if self.token is not None:
            self.pending_mode = mode
        else:
            self.agent.set_mode(mode)
            self.mode, self.pending_mode = mode, None
        self.app.invalidate()

    def cycle_mode(self) -> None:
        current = self.pending_mode or self.mode
        following = CYCLE[(CYCLE.index(current) + 1) % len(CYCLE)] if current in CYCLE else "ask"
        self.set_mode(following)

    async def _undo(self, rest: str) -> None:
        assert self.runtime is not None
        try:
            restored = await undo(log=self.runtime.log, env=self.runtime.env,
                                  checkpoints=self.runtime.checkpoints,
                                  turns=int(rest) if rest else 1)
        except ValueError as exc:
            self.printer.print(Text(f"undo: {exc}", style="red"))
            return
        if restored is None:
            self.printer.print(Text("undo: nothing to undo", style="dim"))

    # -- lifecycle ----------------------------------------------------------------------------

    async def run(self, patch: bool = True) -> int:
        self.runtime = Runtime(self.args, self._on_event, self.provider, self.policy)
        try:
            self.agent = await self.runtime.build_agent(InlineApprover(self), self._on_stream)
            self.mode = self.agent.mode
            self.printer.print(banner(
                self.runtime.session_id, f"{self.args.provider} {self.args.model}",
                self.runtime.env.root, self.mode, bool(self.args.resume),
            ), gap=False)
            if self.args.prompt:
                self.app.pre_run_callables.append(lambda: self.start(self.args.prompt))
            if patch:
                with patch_stdout(raw=True):
                    await self.app.run_async()
            else:
                await self.app.run_async()
            return EXIT_CODES[self.status]
        finally:
            if self.run_task is not None:
                self.run_task.cancel()
                await asyncio.gather(self.run_task, return_exceptions=True)
            await self.runtime.aclose()
            self.runtime.log.close()


