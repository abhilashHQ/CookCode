import io
import json
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trajectories import Trajectory

from cookcode.cli.app import main, parse_args
from cookcode.cli.headless import run_headless
from cookcode.core.events import (
    Event,
    Interrupted,
    ItemEvent,
    ModelRequestStarted,
    Repaired,
    RunConfig,
    SessionStarted,
    UserMessage,
)
from cookcode.core.items import Reasoning, ToolCall, ToolResult
from cookcode.core.usage import Usage
from cookcode.export.atif import ATIF_VERSION, to_atif
from cookcode.providers.fake import FakeProvider, FakeTurn, call, say
from cookcode.redact import Redactor
from cookcode.store.jsonl import JsonlSessionStore


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    (work / "a.txt").write_text("alpha\n")
    monkeypatch.chdir(work)
    monkeypatch.setenv("COOKCODE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GIT_DIR", raising=False)
    return work


async def session(tmp_path: Path, turns: list[FakeTurn], argv: list[str]) -> list[Event]:
    await run_headless(parse_args(argv), provider=FakeProvider(turns), stdout=io.StringIO())
    store = JsonlSessionStore(tmp_path / "home" / "sessions")
    (session_id,) = store.list_sessions()
    return store.read(session_id)


def validate(trajectory: dict[str, Any]) -> Trajectory:
    return Trajectory.model_validate(trajectory)


async def test_tool_round_trip_validates_against_harbor(workspace: Path, tmp_path: Path) -> None:
    read_a = call("read", "call_a", path="a.txt")
    glob = call("glob", "call_b", pattern="*.txt")
    thought = Reasoning(provider="fake", model_family="m", payload={"reasoning_content": "look"})
    events = await session(
        tmp_path,
        [
            FakeTurn(
                items=(thought, read_a, glob), usage=Usage(input=100, cache_read=40, output=9)
            ),
            FakeTurn(items=(say("alpha it is"),), usage=Usage(input=20, output=4)),
        ],
        ["-p", "what is in a.txt", "--mode", "yolo"],
    )
    trajectory = validate(to_atif(events, session_id="s"))
    assert trajectory.schema_version == ATIF_VERSION
    user, first, second = trajectory.steps
    assert (user.source, user.message) == ("user", "what is in a.txt")
    assert first.reasoning_content == "look"
    assert [c.tool_call_id for c in first.tool_calls or []] == ["call_a", "call_b"]
    assert first.observation is not None
    results = {r.source_call_id: r for r in first.observation.results}
    assert "alpha" in str(results["call_a"].content)
    extra = results["call_a"].extra or {}
    assert (extra["status"], extra["exit_status"], sorted(extra)) == (
        "ok", "ok", ["duration_s", "exit_status", "status"]
    )
    assert first.metrics is not None and first.metrics.prompt_tokens == 140
    assert first.metrics.cached_tokens == 40
    assert second.message == "alpha it is" and second.tool_calls is None
    final = trajectory.final_metrics
    assert final is not None
    assert (final.total_prompt_tokens, final.total_completion_tokens) == (160, 13)
    assert trajectory.agent.model_name == "minimaxai/minimax-m3.1-flash"
    assert trajectory.extra == {"harness_schema_version": 1, "status": "done", "runs": 1}


async def test_denied_call_carries_its_approval(workspace: Path, tmp_path: Path) -> None:
    events = await session(
        tmp_path,
        [
            FakeTurn(items=(call("write", "call_w", path="b.txt", content="x"),)),
            FakeTurn(items=(say("could not"),)),
        ],
        ["-p", "write b"],
    )
    trajectory = validate(to_atif(events))
    agent = trajectory.steps[1]
    (tool_call,) = agent.tool_calls or []
    assert tool_call.extra is not None and tool_call.extra["approval"]["decision"] == "deny"
    assert agent.observation is not None
    assert agent.observation.results[0].extra == {"status": "denied"}


def config() -> RunConfig:
    return RunConfig(provider="fake", model="m", harness_version="0.0.1")


def log(tmp_path: Path, bodies: list[Any]) -> list[Event]:
    store = JsonlSessionStore(tmp_path / "s", Redactor())
    with store.open("x", create=True) as session_log:
        for body in bodies:
            session_log.append(body)
        return session_log.read()


def test_repaired_and_interrupted_runs_still_validate(tmp_path: Path) -> None:
    dangling = ToolCall(id="i1", provider_call_id="call_x", name="bash", arguments={})
    broken = ToolCall(
        id="i2", provider_call_id="call_y", name="bash", arguments={}, raw_arguments="{oops"
    )
    events = log(
        tmp_path,
        [
            SessionStarted.for_config(config()),
            UserMessage(text="go"),
            ModelRequestStarted(request_id="r1", view="raw", token_estimate=1),
            ItemEvent(item=dangling, request_id="r1"),
            ItemEvent(item=broken, request_id="r1"),
            Interrupted(phase="tool"),
            Repaired(call_id="i1", status="unknown", reason="may have run"),
            ItemEvent(item=ToolResult(call_id="i1", status="unknown", content="?")),
            ItemEvent(item=ToolResult(call_id="i2", status="error", content="bad args")),
            ItemEvent(item=ToolResult(call_id="ghost", status="error", content="orphan")),
        ],
    )
    trajectory = validate(to_atif(events))
    sources = [step.source for step in trajectory.steps]
    assert sources == ["user", "agent", "system", "system"]
    agent = trajectory.steps[1]
    assert agent.metrics is None
    calls = agent.tool_calls or []
    assert calls[1].arguments == {} and calls[1].extra == {"raw_arguments": "{oops"}
    assert agent.observation is not None
    assert agent.observation.results[0].extra == {"status": "unknown", "repaired": "may have run"}
    assert trajectory.steps[2].message == "interrupted while tool"
    orphan = trajectory.steps[3].observation
    assert orphan is not None and orphan.results[0].source_call_id is None


def test_empty_log_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no messages"):
        to_atif(log(tmp_path, [SessionStarted.for_config(config())]))


async def test_export_command_writes_a_file(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await session(tmp_path, [FakeTurn(items=(say("hi"),))], ["-p", "hello"])
    session_id = JsonlSessionStore(tmp_path / "home" / "sessions").list_sessions()[0]
    target = tmp_path / "trajectory.json"
    assert main(["export", session_id, "-o", str(target)]) == 0
    trajectory = validate(json.loads(target.read_text()))
    assert trajectory.session_id == session_id
    assert main(["export", "missing"]) == 1
    assert "missing" in capsys.readouterr().err
