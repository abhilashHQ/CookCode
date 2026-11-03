"""Full-screen client on Textual: a transcript of widgets, a session panel, a status line, a
prompt and review dialogs for approvals and plans.

It is another observer of the same runtime as the plain CLI (decision 0026), with its own
renderer (0052). Typing while the agent works steers it at the next step boundary; Ctrl-C
stops the run, a second Ctrl-C forces it, and Ctrl-C at the prompt quits. Shift-Tab cycles
the mode between runs."""

import argparse
import asyncio
from typing import Any, ClassVar, Literal

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from cookcode.agent.approval import Decision, Risk
from cookcode.agent.gate import MODES, Mode
from cookcode.agent.loop import Agent
from cookcode.agent.tools import EXIT_PLAN
from cookcode.agent.undo import undo
from cookcode.cli.app import Runtime
from cookcode.cli.terminal import always_rule, call_diff, describe_call
from cookcode.cli.tui_render import ToolBlock, Transcript, diff_text, render_side
from cookcode.core.cancel import CancelToken
from cookcode.core.events import (
    CheckpointCreated,
    Event,
    ModeChanged,
    ModelRequestFinished,
    ModelRequestStarted,
    RunStarted,
    SessionFinished,
)
from cookcode.core.items import ToolCall
from cookcode.core.usage import Usage
from cookcode.providers.base import Provider

Bindings = list[Binding | tuple[str, str] | tuple[str, str, str]]
CYCLE: tuple[Mode, ...] = ("ask", "auto-edit", "plan")
HELP = (
    "/mode <plan|ask|auto-edit|yolo> sets the mode for the next run, /undo [n] reverts file "
    "edits of the last n turns, /exit quits. Shift-Tab cycles the mode, Ctrl-O opens or "
    "closes every tool block, Ctrl-B toggles the panel."
)
FEEDBACK = "feedback"


class ApprovalScreen(ModalScreen[Decision | Literal["feedback"]]):
    BINDINGS: ClassVar[Bindings] = [
        Binding("y", "allow"), Binding("n", "deny"), Binding("a", "always"),
        Binding("escape", "deny"),
    ]

    def __init__(self, call: ToolCall, risk: Risk) -> None:
        super().__init__()
        self._call = call
        self._risk = risk
        self._rule = always_rule(call, risk)

    def compose(self) -> ComposeResult:
        subjects = ", ".join(self._risk.subjects) or "-"
        diff = call_diff(self._call)
        with Vertical(id="approval"):
            yield Static(Text(f"approval needed: {self._risk.reason}", style="bold"))
            yield Static(describe_call(self._call), classes="call")
            yield Static(Text(f"touches {subjects} (cwd {self._risk.cwd})", style="dim"))
            if diff:
                with VerticalScroll(classes="review"):
                    yield Static(diff_text(diff))
            with Horizontal(classes="buttons"):
                yield Button(Text("[y]es"), id="allow", variant="success")
                if self._rule is not None:
                    yield Button(Text(f"[a]lways ({self._rule})"), id="always")
                yield Button(Text("[n]o"), id="deny", variant="error")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        {"allow": self.action_allow, "always": self.action_always,
         "deny": self.action_deny}[event.button.id or "deny"]()

    def action_allow(self) -> None:
        self.dismiss(Decision(action="allow"))

    def action_always(self) -> None:
        if self._rule is not None:
            self.dismiss(Decision(action="allow_always", rule=self._rule))

    def action_deny(self) -> None:
        self.dismiss(FEEDBACK)


class PlanScreen(ModalScreen[Decision | Literal["feedback"]]):
    """The plan review: approve into ask or auto-edit, or keep planning with feedback."""

    BINDINGS: ClassVar[Bindings] = [
        Binding("y", "approve"), Binding("a", "auto"), Binding("n", "revise"),
        Binding("escape", "revise"),
    ]

    def __init__(self, call: ToolCall) -> None:
        super().__init__()
        plan = call.arguments.get("plan")
        self._plan = plan if isinstance(plan, str) else ""

    def compose(self) -> ComposeResult:
        with Vertical(id="approval", classes="plan-review"):
            yield Static(Text("the agent proposes this plan", style="bold"))
            with VerticalScroll(classes="review"):
                yield Static(Markdown(self._plan))
            with Horizontal(classes="buttons"):
                yield Button(Text("[y] approve, ask before edits"), id="approve", variant="success")
                yield Button(Text("[a] approve, auto-edit"), id="auto", variant="primary")
                yield Button(Text("[n] keep planning"), id="revise", variant="error")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        {"approve": self.action_approve, "auto": self.action_auto,
         "revise": self.action_revise}[event.button.id or "revise"]()

    def action_approve(self) -> None:
        self.dismiss(Decision(action="allow"))

    def action_auto(self) -> None:
        self.dismiss(Decision(action="allow_always"))

    def action_revise(self) -> None:
        self.dismiss(FEEDBACK)


class FeedbackScreen(ModalScreen[str]):
    """A second screen for the reason, so typing never collides with the y/n/a keys."""

    BINDINGS: ClassVar[Bindings] = [Binding("escape", "skip")]

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="approval"):
            yield Static(Text(self._label, style="bold"))
            yield Input(placeholder="enter to send, escape to skip", id="feedback")

    def on_mount(self) -> None:
        self.query_one("#feedback", Input).focus()

    @on(Input.Submitted, "#feedback")
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_skip(self) -> None:
        self.dismiss("")


class TuiApprover:
    def __init__(self, app: "CookApp") -> None:
        self._app = app

    async def _show[T](self, screen: ModalScreen[T], default: T) -> T:
        answer: asyncio.Future[T] = asyncio.get_running_loop().create_future()

        def done(value: T | None) -> None:
            if not answer.done():
                answer.set_result(default if value is None else value)

        self._app.push_screen(screen, callback=done)
        return await answer

    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        self._app.waiting = True
        self._app.refresh_status()
        try:
            plan = call.name == EXIT_PLAN
            first: Decision | Literal["feedback"] = await self._show(
                PlanScreen(call) if plan else ApprovalScreen(call, risk), FEEDBACK
            )
            if isinstance(first, Decision):
                return first
            label = "what should change in the plan?" if plan else "reason for the model"
            reason = await self._show(FeedbackScreen(label), "")
            return Decision(action="deny", reason=reason or "the user said no")
        finally:
            self._app.waiting = False
            self._app.refresh_status()


class CookApp(App[int]):
    CSS = """
    #main { height: 1fr; }
    #transcript { width: 1fr; padding: 0 1; }
    #side { width: 30; padding: 0 1; border-left: vkey $panel; color: $text-muted; }
    #status { height: 1; background: $panel; color: $text-muted; }
    .note { margin: 0 0 0 0; }
    .user { color: $text; text-style: bold; margin-top: 1; }
    .steer { color: $accent; }
    .error { color: $error; }
    .ok { color: $success; }
    .dim { color: $text-muted; }
    .mode { color: $warning; }
    .assistant { margin: 1 0 0 0; }
    .tool { margin: 0; padding: 0; border: none; background: transparent; }
    .tool.failed CollapsibleTitle { color: $error; }
    .tool.ok CollapsibleTitle { color: $text-muted; }
    .tool Contents { padding: 0 0 0 2; }
    .thinking { margin: 0; padding: 0; border: none; background: transparent; }
    .thinking CollapsibleTitle { color: $text-muted; }
    .plan { border: round $warning; padding: 0 1; margin: 1 0; }
    .plan.approved { border: round $success; }
    .plan.rejected { border: round $error; }
    #approval { width: 90%; max-height: 90%; height: auto; border: round $warning;
                padding: 1 2; background: $surface; }
    #approval .review { max-height: 24; height: auto; margin: 1 0; }
    #approval .call { color: $accent; }
    #approval .buttons { height: auto; margin-top: 1; }
    #approval Button { margin-right: 1; }
    ApprovalScreen, PlanScreen, FeedbackScreen { align: center middle; }
    """
    BINDINGS: ClassVar[Bindings] = [
        Binding("ctrl+c", "interrupt", priority=True),
        Binding("ctrl+d", "quit"),
        Binding("shift+tab", "cycle_mode", priority=True),
        Binding("ctrl+o", "toggle_tools"),
        Binding("ctrl+b", "toggle_side"),
    ]

    def __init__(self, args: argparse.Namespace, provider: Provider | None = None) -> None:
        super().__init__()
        self.args = args
        self.provider = provider
        self.usage = Usage()
        self.turns = 0
        self.context_tokens = 0
        self.files: dict[str, None] = {}
        self.mode: str = args.mode
        self.state = "ready"
        self.waiting = False
        self.running: CancelToken | None = None
        self.agent: Agent | None = None
        self.runtime: Runtime | None = None
        self._side_hidden = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield Transcript(id="transcript")
            yield Static(id="side")
        yield Static(id="status")
        yield Input(placeholder="message, /mode, /undo [n], /help, /exit", id="prompt")

    @property
    def transcript(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    async def on_mount(self) -> None:
        transcript = self.transcript

        def observe(event: Event) -> None:
            transcript.on_log_event(event)
            self._count(event)

        self.runtime = Runtime(self.args, observe, self.provider)
        self.agent = await self.runtime.build_agent(TuiApprover(self), transcript.on_stream)
        loaded = ", ".join(file.path for file in self.runtime.instructions) or "none"
        transcript.note(
            f"session {self.runtime.session_id} | workspace {self.runtime.env.root} | "
            f"instructions: {loaded}",
            "dim",
        )
        transcript.note(HELP, "dim")
        self.refresh_status()
        self.query_one("#prompt", Input).focus()
        if self.args.prompt:
            self._start(self.args.prompt)

    def on_resize(self, event: Resize) -> None:
        self.query_one("#side").display = not self._side_hidden and event.size.width >= 100

    def _count(self, event: Event) -> None:
        body = event.body
        if isinstance(body, ModelRequestFinished):
            self.usage = self.usage + body.usage
            self.turns += 1
        elif isinstance(body, ModelRequestStarted):
            self.context_tokens = body.token_estimate
        elif isinstance(body, CheckpointCreated):
            self.files.update(dict.fromkeys(body.paths))
        elif isinstance(body, ModeChanged):
            self.mode = body.mode
        elif isinstance(body, RunStarted) and body.config.mode is not None:
            self.mode = body.config.mode
        elif isinstance(body, SessionFinished):
            self.state = body.status
        else:
            return
        self.refresh_status()

    def refresh_status(self) -> None:
        state = "awaiting approval" if self.waiting else (
            "working" if self.running is not None else self.state
        )
        self.query_one("#status", Static).update(
            Text.assemble(
                f" {state} | ", (self.mode, "bold"),
                f" | {self.turns} requests | shift+tab mode  ctrl+o tools  ctrl+c stop",
            )
        )
        usage = self.usage
        files = "\n".join(self.files) or "none yet"
        self.query_one("#side", Static).update(render_side([
            ("mode", Text(self.mode, style="bold")),
            ("model", Text(f"{self.args.provider}\n{self.args.model}")),
            ("usage", Text(
                f"{self.turns} requests\n{usage.total_input} in\n"
                f"{usage.cache_read} cached\n{usage.output} out"
            )),
            ("context", Text(f"~{self.context_tokens} tokens in the last request")),
            ("files changed", Text(files)),
        ]))

    @on(Input.Submitted, "#prompt")
    async def submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if self.running is not None and self.agent is not None:
            self.agent.steer(text)
        elif text == "/exit":
            self.exit(0)
        elif text.startswith("/undo"):
            await self._undo(text.removeprefix("/undo").strip())
        elif text.startswith("/mode"):
            self._set_mode(text.removeprefix("/mode").strip())
        elif text.startswith("/"):
            self.transcript.note(HELP, "dim")
        else:
            self._start(text)

    def _set_mode(self, name: str) -> None:
        modes: dict[str, Mode] = {m: m for m in MODES}
        mode = modes.get(name)
        if mode is None:
            self.transcript.note(f"[mode] pick one of {', '.join(MODES)}", "error")
            return
        if self.running is not None or self.agent is None:
            self.transcript.note("[mode] the mode changes between runs", "error")
            return
        self.agent.set_mode(mode)
        self.mode = mode
        self.transcript.note(f"[mode] {mode} from the next message", "mode")
        self.refresh_status()

    def action_cycle_mode(self) -> None:
        current = self.agent.mode if self.agent is not None else self.mode
        following = CYCLE[(CYCLE.index(current) + 1) % len(CYCLE)] if current in CYCLE else "ask"
        self._set_mode(following)

    def action_toggle_tools(self) -> None:
        blocks = list(self.transcript.query(ToolBlock))
        expand = any(block.collapsed for block in blocks)
        for block in blocks:
            block.collapsed = not expand

    def action_toggle_side(self) -> None:
        self._side_hidden = not self._side_hidden
        self.query_one("#side").display = not self._side_hidden and self.size.width >= 100

    def _start(self, prompt: str) -> None:
        self.running = CancelToken()
        self.refresh_status()
        self.run_worker(self._run(prompt, self.running), exclusive=True, group="agent")

    async def _run(self, prompt: str, token: CancelToken) -> None:
        assert self.agent is not None
        try:
            await self.agent.run(prompt, cancel=token)
        finally:
            self.running = None
            self.refresh_status()

    async def _undo(self, rest: str) -> None:
        assert self.runtime is not None
        try:
            restored = await undo(log=self.runtime.log, env=self.runtime.env,
                                  checkpoints=self.runtime.checkpoints,
                                  turns=int(rest) if rest else 1)
        except ValueError as exc:
            self.transcript.note(f"[undo] {exc}", "error")
            return
        if restored is None:
            self.transcript.note("[undo] nothing to undo", "dim")

    def action_interrupt(self) -> None:
        if self.running is not None and not self.running.cancelled:
            self.transcript.note("[stopping: press Ctrl-C again to force]", "error")
            self.running.cancel("user")
        elif self.running is not None:
            for worker in self.workers:
                if worker.group == "agent":
                    worker.cancel()
        else:
            self.exit(0)

    async def on_unmount(self) -> None:
        if self.runtime is not None:
            await self.runtime.aclose()
            self.runtime.log.close()


async def run_tui(args: argparse.Namespace, provider: Provider | None = None, **_: Any) -> int:
    return await CookApp(args, provider).run_async() or 0
