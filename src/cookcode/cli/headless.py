import argparse
import asyncio
import signal
import sys
import threading
from typing import Literal, TextIO

from pydantic import BaseModel, ConfigDict, ValidationError

from cookcode.agent.approval import Approver, Decision, PolicyOnlyApprover, Risk
from cookcode.agent.loop import RunOutcome
from cookcode.cli.app import (
    EXIT_CODES,
    SESSION_ERRORS,
    Runtime,
    load_policy,
    load_resumed,
    missing_key,
    session_error,
)
from cookcode.core.cancel import CancelToken
from cookcode.core.events import Event, ItemEvent, RunStarted
from cookcode.core.items import Message, ToolCall
from cookcode.providers.base import Provider


class ApprovalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    decision: Literal["allow", "deny", "allow_always"]
    reason: str | None = None
    rule: str | None = None


class LineReader:
    """Reads a stream on a daemon thread, so a pending read never holds up shutdown."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._loop = asyncio.get_running_loop()
        self._lines: asyncio.Queue[str | None] = asyncio.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            line = self._stream.readline()
            try:
                self._loop.call_soon_threadsafe(self._lines.put_nowait, line or None)
            except RuntimeError:
                return
            if not line:
                return

    async def readline(self) -> str | None:
        line = await self._lines.get()
        if line is None:
            self._lines.put_nowait(None)
        return line


class StdinApprover:
    """Answers each open approval with one JSON line from stdin, naming the call it answers."""

    def __init__(self, reader: LineReader) -> None:
        self._reader = reader

    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        line = await self._reader.readline()
        if line is None:
            return Decision(action="deny", reason="stdin closed before an approval answer")
        try:
            answer = ApprovalAnswer.model_validate_json(line)
        except ValidationError as exc:
            error = exc.errors()[0]
            return Decision(action="deny", reason=f"invalid approval answer: {error['msg']}")
        if answer.call_id != call.id:
            return Decision(
                action="deny",
                reason=f"approval answer names call {answer.call_id}, expected {call.id}",
            )
        return Decision(action=answer.decision, reason=answer.reason, rule=answer.rule)


def final_text(events: list[Event]) -> str:
    text = ""
    for event in events:
        body = event.body
        if isinstance(body, RunStarted):
            text = ""
        elif (
            isinstance(body, ItemEvent)
            and isinstance(body.item, Message)
            and body.item.role == "assistant"
        ):
            text = body.item.text
    return text


async def run_headless(
    args: argparse.Namespace,
    *,
    provider: Provider | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    try:
        load_resumed(args)
    except SESSION_ERRORS as exc:
        print(f"cookcode: {session_error(args.resume, exc)}", file=sys.stderr)
        return 1
    if provider is None and (message := missing_key(args)):
        print(f"cookcode: {message}", file=sys.stderr)
        return 1
    try:
        policy = load_policy(args.policy)
    except ValueError as exc:
        print(f"cookcode: {exc}", file=sys.stderr)
        return 1

    def emit(event: Event) -> None:
        out.write(event.model_dump_json() + "\n")
        out.flush()

    try:
        runtime = Runtime(args, emit if args.json else lambda event: None, provider, policy)
    except SESSION_ERRORS as exc:
        print(f"cookcode: {session_error(args.resume, exc)}", file=sys.stderr)
        return 1
    approver: Approver = (
        StdinApprover(LineReader(stdin or sys.stdin))
        if args.approvals == "stdin"
        else PolicyOnlyApprover()
    )
    cancel = CancelToken()
    task: asyncio.Task[RunOutcome] | None = None

    def on_signal() -> None:
        if not cancel.cancelled:
            cancel.cancel("user")
        elif task is not None:
            task.cancel()

    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, on_signal)
    status = "interrupted"
    try:
        agent = await runtime.build_agent(approver)
        task = asyncio.ensure_future(agent.run(args.headless, cancel=cancel))
        try:
            outcome = await task
            status = outcome.status
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        if not args.json:
            out.write(final_text(runtime.log.read()) + "\n")
            out.flush()
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        await runtime.aclose()
        runtime.log.close()
    return EXIT_CODES[status]
