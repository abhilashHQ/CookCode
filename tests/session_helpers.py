import io
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from agent_helpers import assert_every_call_resolved
from harness.agent.context import build_request, items_sha256
from harness.cli.app import main, parse_args
from harness.cli.headless import run_headless
from harness.core.events import (
    Event,
    EventBody,
    ItemEvent,
    ModelRequestFinished,
    ModelRequestStarted,
    RunConfig,
    RunStarted,
    SessionFinished,
    SessionStarted,
    ToolCallStarted,
    UserMessage,
)
from harness.core.items import ToolCall, ToolResult
from harness.core.usage import Usage
from harness.providers.fake import FakeProvider
from harness.store.jsonl import JsonlSessionStore

FAKE_CONFIG = RunConfig(provider="fake", model="fake-1", harness_version="0.0.1")


def make_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    for name in ("GIT_DIR", "HARNESS_PROVIDER", "HARNESS_MODEL", "HARNESS_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return work


def store(tmp_path: Path) -> JsonlSessionStore:
    return JsonlSessionStore(tmp_path / "home" / "sessions")


def only_session(tmp_path: Path) -> str:
    (session,) = store(tmp_path).list_sessions()
    return session


async def headless(argv: list[str], fake: FakeProvider) -> int:
    return await run_headless(
        parse_args(argv), provider=fake, stdin=io.StringIO(), stdout=io.StringIO()
    )


def assert_valid(events: Sequence[Event]) -> None:
    """Every call has exactly one result before the next request and the session ends done."""
    calls: set[str] = set()
    answered: set[str] = set()
    for event in events:
        body = event.body
        if isinstance(body, ModelRequestStarted):
            assert calls <= answered, f"request {body.request_id} with open calls"
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolCall):
            calls.add(body.item.id)
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolResult):
            answered.add(body.item.call_id)
    assert_every_call_resolved(events)
    last = events[-1].body
    assert isinstance(last, SessionFinished) and last.status == "done"


def replay(session: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    capsys.readouterr()
    code = main(["replay", session])
    return code, capsys.readouterr().out.splitlines()


def assert_replays(session: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines = replay(session, capsys)
    assert code == 0, lines
    assert lines and all(line.endswith(" match") for line in lines[:-1]), lines
    assert lines[-1].endswith("0 mismatch, 0 unverified")


def tamper(tmp_path: Path, session: str, nth: int = 0) -> None:
    path = tmp_path / "home" / "sessions" / f"{session}.jsonl"
    lines = path.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if '"model_request_started"' in line]
    record = json.loads(lines[starts[nth]])
    record["body"]["items_sha256"] = "0" * 64
    lines[starts[nth]] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n")


def crashed_session(
    tmp_path: Path, session: str, dangling: ToolCall, side_effect: str | None
) -> None:
    """A log as a crash leaves it: the call was requested and maybe started, never answered."""
    log = store(tmp_path).open(session, create=True)
    try:
        for body in (
            SessionStarted.for_config(FAKE_CONFIG),
            RunStarted.for_config(FAKE_CONFIG),
            UserMessage(text="change the notes"),
        ):
            log.append(body)
        request = build_request(log.read(), model="fake-1", system="", tools=(), provider="fake")
        tail: list[EventBody] = [
            ModelRequestStarted(
                request_id="r1",
                view="raw",
                token_estimate=1,
                items_sha256=items_sha256(request.items),
            ),
            ItemEvent(item=dangling, request_id="r1"),
            ModelRequestFinished(request_id="r1", stop_reason="tool_use", usage=Usage()),
        ]
        if side_effect is not None:
            tail.append(
                ToolCallStarted.model_validate({
                    "call_id": dangling.id,
                    "tool": dangling.name,
                    "arguments": dangling.arguments,
                    "side_effect": side_effect,
                })
            )
        for body in tail:
            log.append(body)
    finally:
        log.close()
