import asyncio
import io
import json
import os
import signal
from pathlib import Path

import httpx
import pytest

from cookcode.agent.approval import Risk
from cookcode.cli.app import main, parse_args, resolve_window
from cookcode.cli.headless import LineReader, StdinApprover, run_headless
from cookcode.core.cancel import CancelToken
from cookcode.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    Event,
    ItemEvent,
    RunConfig,
    RunStarted,
    SessionFinished,
    event_schema,
    from_json_line,
)
from cookcode.core.items import ToolCall, ToolResult
from cookcode.core.usage import Usage
from cookcode.providers.catalog import PRESETS
from cookcode.providers.fake import FakeProvider, FakeTurn, call, say
from cookcode.store.jsonl import JsonlSessionStore

SCHEMA_FILE = Path(__file__).parent.parent / "docs" / "schema" / "events-v1.json"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("COOKCODE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GIT_DIR", raising=False)
    return work


def stored(tmp_path: Path) -> list[Event]:
    store = JsonlSessionStore(tmp_path / "home" / "sessions")
    (session,) = store.list_sessions()
    return store.read(session)


async def headless(
    argv: list[str], fake: FakeProvider, stdin: str = ""
) -> tuple[int, list[str]]:
    out = io.StringIO()
    code = await run_headless(
        parse_args(argv), provider=fake, stdin=io.StringIO(stdin), stdout=out
    )
    return code, out.getvalue().splitlines()


def write_call() -> ToolCall:
    return call("write", path="notes.txt", content="deploy friday\n")


async def test_json_mode_streams_the_log_verbatim(workspace: Path, tmp_path: Path) -> None:
    fake = FakeProvider(
        [
            FakeTurn(items=(call("glob", pattern="*"),), usage=Usage(input=10, output=2)),
            FakeTurn(items=(say("nothing here"),), usage=Usage(input=12, output=3)),
        ]
    )
    code, lines = await headless(["-p", "look around", "--json"], fake)
    assert code == 0
    events = [from_json_line(line) for line in lines]
    assert events == stored(tmp_path)
    assert events[0].body.type == "session_started"
    assert isinstance(events[-1].body, SessionFinished) and events[-1].body.status == "done"
    assert [event.seq for event in events] == list(range(len(events)))


async def test_text_mode_prints_the_final_answer(workspace: Path) -> None:
    fake = FakeProvider([FakeTurn(items=(say("all done"),))])
    code, lines = await headless(["-p", "hi"], fake)
    assert (code, lines) == (0, ["all done"])


async def test_stdin_answer_allows_the_named_call(workspace: Path, tmp_path: Path) -> None:
    write = write_call()
    fake = FakeProvider([FakeTurn(items=(write,)), FakeTurn(items=(say("written"),))])
    answer = json.dumps({"call_id": write.id, "decision": "allow"})
    code, lines = await headless(
        ["-p", "save it", "--json", "--approvals", "stdin"], fake, answer + "\n"
    )
    assert code == 0
    kinds = [json.loads(line)["body"]["type"] for line in lines]
    assert kinds.index("approval_requested") < kinds.index("approval_resolved")
    assert (workspace / "notes.txt").read_text() == "deploy friday\n"


@pytest.mark.parametrize(
    ("stdin", "reason"),
    [
        ("", "stdin closed before an approval answer"),
        ("not json\n", "invalid approval answer"),
        ('{"call_id": "other", "decision": "allow"}\n', "approval answer names call other"),
    ],
)
async def test_bad_or_missing_answers_deny(
    workspace: Path, tmp_path: Path, stdin: str, reason: str
) -> None:
    fake = FakeProvider([FakeTurn(items=(write_call(),)), FakeTurn(items=(say("ok"),))])
    code, _ = await headless(["-p", "save it", "--approvals", "stdin"], fake, stdin)
    assert code == 0
    resolved = [e.body for e in stored(tmp_path) if isinstance(e.body, ApprovalResolved)]
    assert resolved[0].decision == "deny"
    assert reason in (resolved[0].reason or "")
    assert not (workspace / "notes.txt").exists()


async def test_without_an_answer_source_open_approvals_deny(
    workspace: Path, tmp_path: Path
) -> None:
    fake = FakeProvider([FakeTurn(items=(write_call(),)), FakeTurn(items=(say("ok"),))])
    code, _ = await headless(["-p", "save it"], fake)
    assert code == 0
    events = stored(tmp_path)
    assert any(isinstance(e.body, ApprovalRequested) for e in events)
    (result,) = [
        e.body.item
        for e in events
        if isinstance(e.body, ItemEvent) and isinstance(e.body.item, ToolResult)
    ]
    assert result.status == "denied"


async def test_policy_file_allows_without_asking(workspace: Path, tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"allow": ["write:notes.txt"]}))
    fake = FakeProvider([FakeTurn(items=(write_call(),)), FakeTurn(items=(say("ok"),))])
    code, _ = await headless(["-p", "save it", "--policy", str(policy)], fake)
    assert code == 0
    events = stored(tmp_path)
    assert not any(isinstance(e.body, ApprovalRequested) for e in events)
    assert "allow write:notes.txt" in events[0].body.config.policy  # type: ignore[union-attr]
    assert (workspace / "notes.txt").exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"allow": ["no-colon"]}', "is not in the form tool:pattern"),
        ('{"allow": [], "extra": 1}', "cannot load policy"),
        ("{not json", "cannot load policy"),
    ],
)
async def test_bad_policy_file_exits_1(
    workspace: Path,
    tmp_path: Path,
    content: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(content)
    code, lines = await headless(["-p", "x", "--policy", str(policy)], FakeProvider([]))
    assert (code, lines) == (1, [])
    assert message in capsys.readouterr().err


async def test_missing_key_goes_to_stderr(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    out = io.StringIO()
    assert await run_headless(parse_args(["-p", "hi", "--json"]), stdout=out) == 1
    assert out.getvalue() == ""
    assert "set NVIDIA_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_signal_interrupts_with_130(workspace: Path, sig: signal.Signals) -> None:
    fake = FakeProvider([FakeTurn(items=(say("x" * 400),))], chunk_size=1, event_delay_s=0.02)

    async def interrupt() -> None:
        while not fake.requests:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        os.kill(os.getpid(), sig)

    sender = asyncio.create_task(interrupt())
    async with asyncio.timeout(10):
        code, lines = await headless(["-p", "talk", "--json"], fake)
    await sender
    assert code == 130
    last = from_json_line(lines[-1]).body
    assert isinstance(last, SessionFinished) and last.status == "interrupted"


async def test_approver_reads_one_line_per_decision() -> None:
    reader = LineReader(io.StringIO('{"call_id": "a", "decision": "allow_always", "rule": "r"}\n'))
    approver = StdinApprover(reader)
    risk = Risk(side_effect="mutating", rule=None, reason="r", cwd=".", subjects=())
    first = ToolCall(id="a", provider_call_id="p", name="write", arguments={})
    second = ToolCall(id="b", provider_call_id="q", name="write", arguments={})
    decision = await approver.decide(first, risk, CancelToken())
    assert (decision.action, decision.rule) == ("allow_always", "r")
    assert (await approver.decide(second, risk, CancelToken())).action == "deny"


def test_checked_in_schema_matches_the_models() -> None:
    expected = json.loads(SCHEMA_FILE.read_text())
    assert expected == event_schema(), "run: uv run cookcode schema > docs/schema/events-v1.json"


def test_schema_and_flag_errors_from_main(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["$id"] == "cookcode-events-v1"
    assert main(["--json", "hi"]) == 1


def llamacpp(models: list[str], n_ctx: int | None = 4096) -> httpx.MockTransport:
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": m} for m in models]})
        settings = {"n_ctx": n_ctx} if n_ctx is not None else {}
        return httpx.Response(200, json={"default_generation_settings": settings})

    return httpx.MockTransport(serve)


def down() -> httpx.MockTransport:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(refuse)


def run_config(tmp_path: Path) -> RunConfig:
    return next(e.body.config for e in stored(tmp_path) if isinstance(e.body, RunStarted))


async def local(argv: list[str], discovery: httpx.MockTransport) -> int:
    fake = FakeProvider([FakeTurn(items=(say("ok"),))])
    return await run_headless(
        parse_args(["-p", "hi", *argv]), provider=fake, stdout=io.StringIO(), discovery=discovery
    )


def test_window_precedence() -> None:
    preset = PRESETS["llamacpp"]
    assert resolve_window(preset, flag=1000, settings=2000, discovered=3000) == (1000, "flag")
    assert resolve_window(preset, flag=None, settings=2000, discovered=3000) == (2000, "settings")
    assert resolve_window(preset, flag=None, settings=None, discovered=3000) == (3000, "server")
    assert resolve_window(preset, flag=None, settings=None, discovered=None) == (4096, "preset")


async def test_cloud_run_config_is_unchanged_but_for_provenance(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeProvider([FakeTurn(items=(say("ok"),))])
    assert (await headless(["-p", "hi", "--strategy", "hybrid"], fake))[0] == 0
    config = run_config(tmp_path)
    assert config.strategy.label == "hybrid:k6:b8:t48000"
    assert (config.context_window, config.context_source) == (128_000, "preset")
    assert capsys.readouterr().err == ""


async def test_local_model_is_picked_when_one_is_served(workspace: Path, tmp_path: Path) -> None:
    assert await local(["--provider", "llamacpp", "--strategy", "hybrid"], llamacpp(["m"])) == 0
    config = run_config(tmp_path)
    assert config.model == "m"
    assert (config.context_window, config.context_source) == (4096, "server")
    assert config.strategy.trigger_tokens == 2048


async def test_flag_beats_the_server(workspace: Path, tmp_path: Path) -> None:
    argv = ["--provider", "llamacpp", "--model", "m", "--context-window", "9000"]
    assert await local(argv, llamacpp(["m"])) == 0
    config = run_config(tmp_path)
    assert (config.context_window, config.context_source) == (9000, "flag")


async def test_several_models_ask_for_one(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await local(["--provider", "llamacpp"], llamacpp(["a", "b"])) == 1
    assert capsys.readouterr().err.strip() == (
        "cookcode: pick a model with --model; http://localhost:8080/v1 serves: a, b"
    )


async def test_server_down_without_model_fails_clearly(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await local(["--provider", "ollama"], down()) == 1
    err = capsys.readouterr().err.strip()
    assert err.startswith("cookcode: no model is loaded at http://localhost:11434/v1: cannot reach")


async def test_server_down_with_a_model_falls_back_to_the_preset(
    workspace: Path, tmp_path: Path
) -> None:
    assert await local(["--provider", "llamacpp", "--model", "m"], down()) == 0
    config = run_config(tmp_path)
    assert (config.context_window, config.context_source) == (4096, "preset")


async def test_small_window_notice(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert await local(["--provider", "llamacpp", "--model", "m"], llamacpp(["m"])) == 0
    assert capsys.readouterr().err.strip() == (
        "cookcode: the context window is 4096 tokens and the raw strategy never summarizes; "
        "long runs will overflow. Try --strategy hybrid."
    )
    argv = ["--provider", "llamacpp", "--model", "m", "--strategy", "hybrid"]
    assert await local(argv, llamacpp(["m"])) == 0
    assert capsys.readouterr().err == ""
