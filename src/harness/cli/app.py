import argparse
import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from harness.agent.approval import Approver
from harness.agent.gate import Policy, Rule, parse_rule
from harness.agent.instructions import InstructionFile, load_instructions, render_system
from harness.agent.loop import Agent, AgentConfig, RunOutcome
from harness.agent.tools import ToolRegistry
from harness.agent.undo import undo
from harness.cli.observe import DisplayProvider, ObservedLog
from harness.cli.terminal import (
    DIM,
    RED,
    Display,
    Terminal,
    TerminalApprover,
)
from harness.core.cancel import CancelToken
from harness.core.events import Event, Limits, RunConfig, RunStarted, SessionStarted
from harness.core.features import Features
from harness.core.ids import new_id
from harness.core.strategy import Strategy
from harness.env.local import LocalEnvironment
from harness.providers.base import Provider, StreamEvent
from harness.providers.gemini import GEMINI_BASE_URL, GeminiProvider
from harness.providers.openai_compat import (
    NIM_BASE_URL,
    OPENROUTER_BASE_URL,
    OpenAICompatProvider,
)
from harness.redact import Redactor
from harness.store.base import (
    SessionCorruptError,
    SessionLockedError,
    SessionNotFoundError,
)
from harness.store.checkpoints import FileCheckpointStore
from harness.store.jsonl import JsonlSessionStore
from harness.tools import default_tools

PRESETS = {
    "nim": (NIM_BASE_URL, "NVIDIA_API_KEY", "minimaxai/minimax-m3.1-flash"),
    "openrouter": (OPENROUTER_BASE_URL, "OPENROUTER_API_KEY", "cohere/north-mini-code:free"),
    "gemini": (GEMINI_BASE_URL, "GEMINI_API_KEY", "gemini-3.5-flash"),
}


def make_provider(
    preset: str, *, api_key: str | None, base_url: str, **transport_policy: Any
) -> Provider:
    if preset == "gemini":
        return GeminiProvider(api_key=api_key, base_url=base_url, name=preset, **transport_policy)
    return OpenAICompatProvider(api_key=api_key, base_url=base_url, name=preset, **transport_policy)


SECRET_ENV = (
    "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
)
OVERRIDABLE = ("provider", "model", "base_url", "api_key_env")
SESSION_ERRORS = (SessionNotFoundError, SessionLockedError, SessionCorruptError, ValueError)
EXIT_CODES = {"done": 0, "error": 1, "budget_exceeded": 2, "interrupted": 130}
HELP = "commands: /undo [n] reverts file edits of the last n turns, /exit quits"

BASE_PROMPT = """\
You are a coding agent working in a local repository.
Workspace root: {root}
Starting working directory (relative to the root): {cwd}

Use the tools to inspect and change files and to run commands. Read a file before you edit it, \
and copy old_string exactly from a fresh read. Prefer small, targeted edits. Run the project's \
tests or a quick check after changing code when that is possible.

Some tool results start with a status in brackets. [error] and [denied] mean the call did not \
do what you asked. [interrupted] means it did not finish and is safe to retry. [unknown] means \
it may have changed state: inspect before you retry it.

When the task is done, reply with a short summary of what you changed and stop calling tools."""
UNATTENDED = "\n\nNobody will answer questions during the task. Work until it is done, then stop."


def _workspace(start: Path) -> Path:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return start
    return Path(top) if top else start


def data_dir() -> Path:
    configured = os.environ.get("HARNESS_HOME")
    if configured:
        return Path(configured)
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "harness"


class PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = []
    deny: list[str] = []


def _rules(texts: list[str]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for text in texts:
        rule = parse_rule(text)
        if rule is None:
            raise ValueError(f"policy rule {text!r} is not in the form tool:pattern")
        rules.append(rule)
    return tuple(rules)


def load_policy(path: str | None) -> Policy:
    if path is None:
        return Policy()
    try:
        data = PolicyFile.model_validate_json(Path(path).read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError(f"cannot load policy {path}: {exc}") from exc
    return Policy(allow=_rules(data.allow), deny=_rules(data.deny))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="harness",
        description=(
            "Coding agent. Other commands: harness schema, harness export <session>, "
            "harness sessions, harness resume <session>, harness fork <session>, "
            "harness replay <session>."
        ),
    )
    parser.add_argument("prompt", nargs="?", help="first prompt; omit to start at the prompt")
    parser.add_argument(
        "-p", "--print", dest="headless", metavar="PROMPT", help="run one prompt headless"
    )
    parser.add_argument(
        "--json", action="store_true", help="headless: write every event as one JSON line"
    )
    parser.add_argument(
        "--approvals",
        choices=("policy", "stdin"),
        default="policy",
        help="headless: who answers approvals the policy leaves open",
    )
    parser.add_argument("--policy", help="JSON file with allow and deny rules (tool:pattern)")
    parser.add_argument("--provider", choices=tuple(PRESETS))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--mode", choices=("ask", "auto-edit", "yolo"), default="ask")
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument(
        "--strategy",
        type=Strategy.parse,
        default=Strategy(),
        help="context strategy: raw, mask, mask_batched, summarize or hybrid, with options "
        "like mask:k6 or hybrid:k6:b8:t48000",
    )
    parser.add_argument(
        "--features",
        type=Features.parse,
        default=Features(),
        help="optional features to turn on, comma separated: jobs",
    )
    parser.add_argument("--once", action="store_true", help="exit after the first run")
    parser.add_argument("--resume", metavar="SESSION", help="continue an existing session")
    parser.add_argument(
        "--continue",
        dest="continue_turn",
        action="store_true",
        help="with --resume: finish the interrupted turn headless, without a new prompt",
    )
    args = parser.parse_args(argv)
    if args.continue_turn and args.resume is None:
        parser.error("--continue needs --resume <session>")
    if args.continue_turn and args.headless is not None:
        parser.error("--continue and -p are exclusive")
    if os.environ.get("HARNESS_PROVIDER", "nim") not in PRESETS:
        parser.error(f"HARNESS_PROVIDER must be one of {', '.join(PRESETS)}")
    args.overrides = {
        key: value for key in OVERRIDABLE if (value := getattr(args, key)) is not None
    }
    apply_provider(args)
    return args


def apply_provider(args: argparse.Namespace, recorded: RunConfig | None = None) -> None:
    """Flags win, then the resumed session's config, then the environment, then the preset.
    The session's model only carries over while its provider does."""
    given: dict[str, str] = args.overrides
    session = recorded.provider if recorded is not None and recorded.provider in PRESETS else None
    args.provider = given.get("provider") or session or os.environ.get("HARNESS_PROVIDER", "nim")
    base_url, key_env, model = PRESETS[args.provider]
    args.base_url = given.get("base_url") or os.environ.get("HARNESS_BASE_URL") or base_url
    args.api_key_env = given.get("api_key_env") or key_env
    same = recorded is not None and given.get("provider", recorded.provider) == recorded.provider
    args.model = (
        given.get("model")
        or (recorded.model if recorded is not None and same else None)
        or os.environ.get("HARNESS_MODEL")
        or model
    )


def session_config(events: Sequence[Event]) -> RunConfig | None:
    for event in reversed(events):
        if isinstance(event.body, RunStarted | SessionStarted):
            return event.body.config
    return None


def session_error(session_id: str, exc: Exception) -> str:
    if isinstance(exc, SessionNotFoundError):
        return f"no session {session_id}"
    if isinstance(exc, SessionLockedError):
        return f"session {session_id} is open in another process"
    return str(exc)


def load_resumed(args: argparse.Namespace) -> None:
    if args.resume is not None:
        events = JsonlSessionStore(data_dir() / "sessions").read(args.resume)
        apply_provider(args, session_config(events))


class Runtime:
    """What every client shares: workspace, session store, environment and the agent factory."""

    def __init__(
        self,
        args: argparse.Namespace,
        sink: Callable[[Event], None],
        provider: Provider | None = None,
        policy: Policy | None = None,
    ) -> None:
        self.args = args
        self.provider = provider
        self.policy = policy if policy is not None else load_policy(args.policy)
        cwd_path = Path.cwd()
        root = _workspace(cwd_path)
        self.cwd = os.path.relpath(cwd_path, root)
        data = data_dir()
        self.session_id: str = args.resume or new_id()
        store = JsonlSessionStore(data / "sessions", Redactor.from_env(SECRET_ENV, os.environ))
        self.log = ObservedLog(store.open(self.session_id, create=args.resume is None), sink)
        self.checkpoints = FileCheckpointStore(data / "checkpoints" / self.session_id)
        self.env = LocalEnvironment(root)
        self.instructions: list[InstructionFile] = []

    async def build_agent(
        self, approver: Approver, stream_sink: Callable[[StreamEvent], None] | None = None
    ) -> Agent:
        self.instructions = await load_instructions(self.env, self.cwd)
        system = render_system(
            BASE_PROMPT.format(root=self.env.root, cwd=self.cwd), self.instructions
        )
        provider: Provider = self.provider or make_provider(
            self.args.provider,
            api_key=os.environ.get(self.args.api_key_env),
            base_url=self.args.base_url,
        )
        if stream_sink is not None:
            provider = DisplayProvider(provider, stream_sink)
        config = AgentConfig(
            model=self.args.model,
            system_prompt=system,
            limits=Limits(max_turns=self.args.max_turns),
            cwd=self.cwd,
            mode=self.args.mode,
            policy=self.policy,
            strategy=self.args.strategy,
        )
        return Agent(
            log=self.log,
            provider=provider,
            env=self.env,
            tools=ToolRegistry(
                default_tools(recall=self.args.strategy.masks, jobs=self.args.features.jobs)
            ),
            config=config,
            approver=approver,
            checkpoints=self.checkpoints,
        )


class Session:
    def __init__(
        self,
        args: argparse.Namespace,
        terminal: Terminal,
        provider: Provider | None = None,
        policy: Policy | None = None,
    ) -> None:
        self.args = args
        self.t = terminal
        self.display = Display(terminal)
        self.runtime = Runtime(args, self.display.on_event, provider, policy)
        self.session_id = self.runtime.session_id
        self.log = self.runtime.log
        self.env = self.runtime.env
        self.checkpoints = self.runtime.checkpoints
        self.cwd = self.runtime.cwd
        self.running: CancelToken | None = None
        self.run_task: asyncio.Task[RunOutcome] | None = None
        self.read_task: asyncio.Task[str | None] | None = None

    @property
    def instructions(self) -> list[InstructionFile]:
        return self.runtime.instructions

    async def build_agent(self) -> Agent:
        return await self.runtime.build_agent(TerminalApprover(self.t), self.display.on_stream)

    def on_interrupt(self) -> None:
        if self.running is not None and not self.running.cancelled:
            self.t.line("\n[stopping: press Ctrl-C again to force]", RED)
            self.running.cancel("user")
        elif self.run_task is not None and not self.run_task.done():
            self.run_task.cancel()
        elif self.read_task is not None and not self.read_task.done():
            self.read_task.cancel()

    async def run(self, agent: Agent, prompt: str) -> RunOutcome | None:
        self.running = CancelToken()
        self.run_task = asyncio.ensure_future(agent.run(prompt, cancel=self.running))
        try:
            return await self.run_task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            self.t.line("[forced stop: the next run repairs the log]", RED)
            return None
        finally:
            self.running = None
            self.run_task = None

    async def read(self) -> str | None:
        self.read_task = asyncio.ensure_future(self.t.readline("\n> "))
        try:
            return await self.read_task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            self.t.line()
            return None
        finally:
            self.read_task = None

    async def command(self, line: str) -> None:
        name, _, rest = line.partition(" ")
        if name == "/undo":
            try:
                turns = int(rest) if rest.strip() else 1
                restored = await undo(
                    log=self.log, env=self.env, checkpoints=self.checkpoints, turns=turns
                )
            except ValueError as exc:
                self.t.line(f"[undo] {exc}", RED)
                return
            if restored is None:
                self.t.line("[undo] nothing to undo", DIM)
        else:
            self.t.line(HELP, DIM)

    def banner(self) -> None:
        loaded = ", ".join(file.path for file in self.instructions) or "none"
        resumed = " (resumed)" if self.args.resume else ""
        self.t.line(
            f"session {self.session_id}{resumed} | {self.args.provider} {self.args.model} | "
            f"mode {self.args.mode}\n"
            f"workspace {self.env.root} (cwd {self.cwd}) | instructions: {loaded}\n{HELP}",
            DIM,
        )


async def run_cli(
    args: argparse.Namespace,
    *,
    provider: Provider | None = None,
    terminal: Terminal | None = None,
) -> int:
    try:
        load_resumed(args)
    except SESSION_ERRORS as exc:
        print(f"harness: {session_error(args.resume, exc)}", file=sys.stderr)
        return 1
    if provider is None and not os.environ.get(args.api_key_env):
        print(f"harness: set {args.api_key_env} to an API key for {args.base_url}")
        return 1
    try:
        policy = load_policy(args.policy)
    except ValueError as exc:
        print(f"harness: {exc}")
        return 1
    terminal = terminal or Terminal()
    try:
        session = Session(args, terminal, provider, policy)
    except SESSION_ERRORS as exc:
        print(f"harness: {session_error(args.resume, exc)}", file=sys.stderr)
        return 1
    await terminal.start()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, session.on_interrupt)
    status = "done"
    try:
        agent = await session.build_agent()
        session.banner()
        prompt: str | None = args.prompt
        while True:
            if prompt is None:
                if args.once:
                    break
                prompt = await session.read()
                if prompt is None or prompt.strip() == "/exit":
                    break
            prompt = prompt.strip()
            if prompt.startswith("/"):
                await session.command(prompt)
            elif prompt:
                outcome = await session.run(agent, prompt)
                status = outcome.status if outcome is not None else "interrupted"
                if args.once:
                    break
            prompt = None
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        session.log.close()
        terminal.close()
    return EXIT_CODES[status]


def main(argv: list[str] | None = None) -> int:
    from harness.cli import commands, headless

    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in commands.COMMANDS:
        return commands.COMMANDS[argv[0]](argv[1:])
    args = parse_args(argv)
    if args.headless is not None or args.continue_turn:
        return asyncio.run(headless.run_headless(args))
    if args.json:
        print("harness: --json needs -p <prompt> or --continue", file=sys.stderr)
        return 1
    return asyncio.run(run_cli(args))
