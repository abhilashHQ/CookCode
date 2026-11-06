import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from cookcode.core.cancel import CancelToken
from cookcode.core.items import Item, Reasoning, ToolCall, ToolResult, text_message
from cookcode.providers.base import (
    ItemDone,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    ToolSchema,
    UsageReport,
)
from cookcode.providers.cassette import (
    Cassette,
    CassetteMismatch,
    RecordingTransport,
    ReplayTransport,
    differences,
    first_difference,
)
from cookcode.providers.gemini import FOREIGN_SIGNATURE, GEMINI_BASE_URL, GeminiProvider
from cookcode.providers.http import StreamingHttpProvider
from cookcode.providers.openai_compat import (
    NIM_BASE_URL,
    OPENROUTER_BASE_URL,
    OpenAICompatProvider,
)
from cookcode.redact import Redactor
from openai_helpers import Script, ok, provider, text_stream

CASSETTES = Path(__file__).parent / "cassettes"
RECORD = os.environ.get("COOKCODE_RECORD") == "1"
READ_TOOL = ToolSchema(
    name="read",
    description="Read a text file and return its contents.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path."}},
        "required": ["path"],
    },
)
SYSTEM = "You are a coding agent. Use the tools to answer. Keep replies to one short line."


# --- the machinery ----------------------------------------------------------------------


async def run(source: StreamingHttpProvider, request: ModelRequest) -> list[StreamEvent]:
    return [event async for event in source.stream(request, CancelToken())]


def simple_request(text: str = "hi") -> ModelRequest:
    return ModelRequest(model="m", system="s", items=(text_message("user", text),))


async def test_a_recorded_exchange_replays_to_the_same_events(tmp_path: Path) -> None:
    cassette = Cassette()
    inner = Script([ok(text_stream("recorded answer"))]).transport
    live = await run(provider(Script([]), transport=RecordingTransport(cassette, inner)),
                     simple_request())
    path = tmp_path / "tape.json"
    cassette.save(path)
    replay = ReplayTransport(Cassette.load(path))
    replayed = await run(provider(Script([]), transport=replay), simple_request())
    replay.assert_exhausted()
    assert [e for e in replayed if not isinstance(e, ItemDone)] == [
        e for e in live if not isinstance(e, ItemDone)
    ]
    assert [e.item.model_dump(exclude={"id"}) for e in replayed if isinstance(e, ItemDone)] == [
        e.item.model_dump(exclude={"id"}) for e in live if isinstance(e, ItemDone)
    ]


async def test_replay_refuses_a_request_body_that_drifted(tmp_path: Path) -> None:
    cassette = Cassette()
    inner = Script([ok(text_stream("x"))]).transport
    await run(provider(Script([]), transport=RecordingTransport(cassette, inner)),
              simple_request("first"))
    events = await run(provider(Script([]), transport=ReplayTransport(cassette)),
                       simple_request("second"))
    end = events[-1]
    assert isinstance(end, StreamEnd) and end.stop_reason == "error"
    assert end.error is not None
    assert "$.messages[1].content: expected 'first', got 'second'" in end.error


async def test_replay_reports_unrecorded_and_unused_requests() -> None:
    replay = ReplayTransport(Cassette())
    events = await run(provider(Script([]), transport=replay), simple_request())
    end = events[-1]
    assert isinstance(end, StreamEnd) and end.error is not None
    assert "was not recorded" in end.error
    with pytest.raises(CassetteMismatch, match="never made"):
        ReplayTransport(Cassette([{"request": {}, "response": {}}])).assert_exhausted()


def test_saved_cassettes_are_redacted(tmp_path: Path) -> None:
    secret = "sk-or-v1-" + "a" * 40
    cassette = Cassette([{"request": {"json": {"note": secret}}, "response": {"body": secret}}])
    path = tmp_path / "tape.json"
    cassette.save(path, Redactor())
    assert secret not in path.read_text()


def test_first_difference_names_the_path() -> None:
    assert first_difference({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}) == (
        "$.a[1].b: expected 2, got 3"
    )
    assert first_difference({"a": 1}, {"a": 1, "c": 2}) == "$.c: unexpected"
    assert first_difference([1], [1, 2]) == "$: expected 1 entries, got 2"
    assert first_difference({"a": 1}, {"a": 1}) is None


def test_differences_lists_every_path_not_just_the_first() -> None:
    expected = {"a": [1, 2, 3], "b": "x", "c": 1}
    actual = {"a": [1, 9, 3, 4], "b": "y", "d": 1}
    assert list(differences(expected, actual)) == [
        "$.a[1]: expected 2, got 9",
        "$.a: expected 3 entries, got 4",
        "$.b: expected 'x', got 'y'",
        "$.c: missing",
        "$.d: unexpected",
    ]


async def test_sequenced_replay_reports_each_difference_once() -> None:
    def interaction(*contents: str) -> dict[str, object]:
        messages = [{"role": "tool", "content": c} for c in contents]
        return {
            "request": {"method": "POST", "url": "https://x/v1", "json": {"messages": messages}},
            "response": {"status": 200, "headers": {}, "body": ""},
        }

    def request(*contents: str) -> httpx.Request:
        messages = [{"role": "tool", "content": c} for c in contents]
        return httpx.Request("POST", "https://x/v1", json={"messages": messages})

    replay = ReplayTransport(
        Cassette([interaction("a"), interaction("a", "b"), interaction("a", "b", "c")]),
        strict=False,
    )
    await replay.handle_async_request(request("A"))
    await replay.handle_async_request(request("A", "b"))
    await replay.handle_async_request(request("A", "b", "C"))
    assert replay.drift == [
        "request #1 body differs at $.messages[0].content: expected 'a', got 'A'",
        "request #3 body differs at $.messages[2].content: expected 'c', got 'C'",
    ]


# --- recorded against OpenRouter, replayed offline ---------------------------------------


@dataclass
class Tape:
    provider: StreamingHttpProvider
    path: Path


PROVIDERS = {
    "openrouter": (OPENROUTER_BASE_URL, "OPENROUTER_API_KEY"),
    "nim": (NIM_BASE_URL, "NVIDIA_API_KEY"),
    "gemini": (GEMINI_BASE_URL, "GEMINI_API_KEY"),
}


@pytest.fixture
def tape(request: pytest.FixtureRequest) -> Iterator[Tape]:
    provider_name = getattr(request, "param", "openrouter")
    base_url, key_env = PROVIDERS[provider_name]
    name = request.node.name.replace("[", "-").replace("]", "").replace("/", "_").replace(":", "_")
    path = CASSETTES / f"{name}.json"
    if RECORD:
        cassette = Cassette()
        transport: httpx.AsyncBaseTransport = RecordingTransport(cassette)
        key = os.environ[key_env]
    else:
        if not path.exists():
            pytest.fail(f"missing cassette {path.name}; record with COOKCODE_RECORD=1")
        cassette = Cassette.load(path)
        transport = ReplayTransport(cassette)
        key = "replay"
    kind = GeminiProvider if provider_name == "gemini" else OpenAICompatProvider
    source = kind(
        api_key=key,
        base_url=base_url,
        name=provider_name,
        transport=transport,
        backoff_base_s=0.0 if not RECORD else 2.0,
    )
    yield Tape(provider=source, path=path)
    if RECORD:
        cassette.save(path, Redactor([key]))
    else:
        assert isinstance(transport, ReplayTransport)
        transport.assert_exhausted()


MODELS = ["cohere/north-mini-code:free", "nvidia/nemotron-3.5-lightning:free"]


def items_of(events: list[StreamEvent]) -> list[Item]:
    return [event.item for event in events if isinstance(event, ItemDone)]


@pytest.mark.parametrize("model", MODELS)
async def test_openrouter_tool_round_trip(tape: Tape, model: str) -> None:
    await round_trip(tape, model)


@pytest.mark.parametrize("tape", ["nim"], indirect=True)
@pytest.mark.parametrize("model", ["minimaxai/minimax-m3.1-flash"])
async def test_nim_tool_round_trip(tape: Tape, model: str) -> None:
    await round_trip(tape, model)


@pytest.mark.parametrize("tape", ["gemini"], indirect=True)
@pytest.mark.parametrize("model", ["gemini-3.5-flash"])
async def test_gemini_signed_round_trip(tape: Tape, model: str) -> None:
    await round_trip(tape, model)
    if RECORD:
        return
    second = Cassette.load(tape.path).interactions[1]["request"]["json"]
    model_turn = second["contents"][1]
    signed = [part for part in model_turn["parts"] if "thoughtSignature" in part]
    assert signed and "functionCall" in signed[0]
    assert signed[0]["thoughtSignature"] != FOREIGN_SIGNATURE


async def round_trip(tape: Tape, model: str) -> None:
    ask = text_message("user", "Read notes.txt and todo.txt, then tell me what notes.txt says.")
    first = await run(
        tape.provider, ModelRequest(model=model, system=SYSTEM, tools=(READ_TOOL,), items=(ask,))
    )
    assert first[-1] == StreamEnd(stop_reason="tool_use")
    turn = items_of(first)
    calls = [item for item in turn if isinstance(item, ToolCall)]
    assert calls and all(call.name == "read" for call in calls)
    assert any(isinstance(item, Reasoning) for item in turn)
    usage = next(event for event in first if isinstance(event, UsageReport))
    assert usage.usage.input > 0 and usage.raw

    def result(call: ToolCall) -> ToolResult:
        if "notes" in str(call.arguments.get("path")):
            return ToolResult(call_id=call.id, status="ok", content="the deploy is on friday")
        return ToolResult(
            call_id=call.id,
            status="interrupted",
            content="not executed: the session stopped before this call ran. Safe to issue again.",
        )

    history = (ask, *turn, *(result(call) for call in calls))
    second = await run(
        tape.provider, ModelRequest(model=model, system=SYSTEM, tools=(READ_TOOL,), items=history)
    )
    end = second[-1]
    assert isinstance(end, StreamEnd) and end.stop_reason in ("end_turn", "tool_use")
    assert end.error is None
