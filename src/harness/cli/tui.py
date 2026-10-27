"""Full-screen client on Textual: transcript, status line, prompt and an approval dialog.

It is another observer of the same runtime as the plain CLI (decision 0026): the plain CLI's
Display renders events and stream text into the transcript pane through a terminal whose
output is the pane. Typing while the agent works steers it at the next step boundary; Ctrl-C
stops the run, a second Ctrl-C forces it, and Ctrl-C at the prompt quits."""

import argparse
import asyncio
import io
from typing import Any, ClassVar

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RichLog, Static

from harness.agent.approval import Decision, Risk
from harness.agent.loop import Agent
from harness.agent.undo import undo
from harness.cli.app import HELP, Runtime
from harness.cli.terminal import Display, Terminal, always_rule, describe_call
from harness.core.cancel import CancelToken
from harness.core.events import Event, ModelRequestFinished, SessionFinished
from harness.core.items import ToolCall
from harness.core.usage import Usage
from harness.providers.base import Provider


class _PaneWriter(io.StringIO):
    """Text written by Display, line by line into the pane, keeping its ANSI colors."""

    def __init__(self, pane: RichLog) -> None:
        self._pane = pane
        self._partial = ""

    def write(self, text: str) -> int:
        self._partial += text
        *lines, self._partial = self._partial.split("\n")
        for line in lines:
            self._pane.write(Text.from_ansi(line))
        return len(text)

    def isatty(self) -> bool:
        return True

    def flush(self) -> None:
        return None


class ApprovalScreen(ModalScreen[Decision]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("y", "allow"), Binding("n", "deny"), Binding("a", "always")
    ]

    def __init__(self, call: ToolCall, risk: Risk) -> None:
        super().__init__()
        self._call = call
        self._risk = risk
        self._rule = always_rule(call, risk)

    def compose(self) -> ComposeResult:
        subjects = ", ".join(self._risk.subjects) or "-"
        with Vertical(id="approval"):
            yield Static(f"approval needed: {self._risk.reason}")
            yield Static(describe_call(self._call))
            yield Static(f"touches {subjects} (cwd {self._risk.cwd})")
            with Horizontal():
                yield Button("[y]es", id="allow", variant="success")
                if self._rule is not None:
                    yield Button(f"[a]lways ({self._rule})", id="always")
                yield Button("[n]o", id="deny", variant="error")

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
        self.dismiss(Decision(action="deny", reason="the user said no"))


class TuiApprover:
    def __init__(self, app: "CookApp") -> None:
        self._app = app

    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        answer: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()

        def done(decision: Decision | None) -> None:
            if not answer.done():
                answer.set_result(decision or Decision(action="deny", reason="dismissed"))

        self._app.push_screen(ApprovalScreen(call, risk), callback=done)
        return await answer


class CookApp(App[int]):
    CSS = """
    #log { height: 1fr; }
    #status { height: 1; background: $panel; color: $text-muted; }
    #approval { width: 90%; height: auto; border: round $warning; padding: 1 2; }
    ApprovalScreen { align: center middle; }
    """
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+c", "interrupt", priority=True), Binding("ctrl+d", "quit")
    ]

    def __init__(self, args: argparse.Namespace, provider: Provider | None = None) -> None:
        super().__init__()
        self.args = args
        self.provider = provider
        self.usage = Usage()
        self.turns = 0
        self.running: CancelToken | None = None
        self.agent: Agent | None = None
        self.runtime: Runtime | None = None
        self.display_: Display | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="log", wrap=True, markup=False)
        yield Static(id="status")
        yield Input(placeholder="message, /undo [n], /exit", id="prompt")

    async def on_mount(self) -> None:
        pane = self.query_one("#log", RichLog)
        display = Display(Terminal(out=_PaneWriter(pane)))
        self.display_ = display

        def observe(event: Event) -> None:
            display.on_event(event)
            self._count(event)

        self.runtime = Runtime(self.args, observe, self.provider)
        self.agent = await self.runtime.build_agent(TuiApprover(self), display.on_stream)
        loaded = ", ".join(file.path for file in self.runtime.instructions) or "none"
        pane.write(Text(f"session {self.runtime.session_id} | {self.args.provider} "
                        f"{self.args.model} | mode {self.args.mode} | instructions: {loaded}",
                        style="dim"))
        pane.write(Text(HELP, style="dim"))
        self._status()
        self.query_one("#prompt", Input).focus()
        if self.args.prompt:
            self._start(self.args.prompt)

    def _count(self, event: Event) -> None:
        if isinstance(event.body, ModelRequestFinished):
            self.usage = self.usage + event.body.usage
            self.turns += 1
            self._status()
        elif isinstance(event.body, SessionFinished):
            self._status(event.body.status)

    def _status(self, state: str | None = None) -> None:
        now = state or ("working" if self.running is not None else "ready")
        self.query_one("#status", Static).update(
            f" {now} | {self.args.model} | {self.turns} requests | "
            f"{self.usage.total_input} in ({self.usage.cache_read} cached) / "
            f"{self.usage.output} out"
        )

    @on(Input.Submitted, "#prompt")
    async def submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if self.running is not None and self.agent is not None:
            self.agent.steer(text)
            self.query_one("#log", RichLog).write(Text(f"[steer] {text}", style="cyan"))
        elif text == "/exit":
            self.exit(0)
        elif text.startswith("/undo"):
            await self._undo(text.removeprefix("/undo").strip())
        elif text.startswith("/"):
            self.query_one("#log", RichLog).write(Text(HELP, style="dim"))
        else:
            self._start(text)

    def _start(self, prompt: str) -> None:
        self.query_one("#log", RichLog).write(Text(f"> {prompt}", style="bold"))
        self.running = CancelToken()
        self._status()
        self.run_worker(self._run(prompt, self.running), exclusive=True, group="agent")

    async def _run(self, prompt: str, token: CancelToken) -> None:
        assert self.agent is not None
        try:
            await self.agent.run(prompt, cancel=token)
        finally:
            self.running = None
            self._status()

    async def _undo(self, rest: str) -> None:
        assert self.runtime is not None
        pane = self.query_one("#log", RichLog)
        try:
            restored = await undo(log=self.runtime.log, env=self.runtime.env,
                                  checkpoints=self.runtime.checkpoints,
                                  turns=int(rest) if rest else 1)
        except ValueError as exc:
            pane.write(Text(f"[undo] {exc}", style="red"))
            return
        if restored is None:
            pane.write(Text("[undo] nothing to undo", style="dim"))

    def action_interrupt(self) -> None:
        if self.running is not None and not self.running.cancelled:
            self.query_one("#log", RichLog).write(
                Text("[stopping: press Ctrl-C again to force]", style="red"))
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
