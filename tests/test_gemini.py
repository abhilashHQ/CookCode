import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from harness.agent.loop import Agent, AgentConfig
from harness.agent.tools import ToolRegistry
from harness.core.cancel import CancelToken
from harness.core.events import ModelRequestStarted
from harness.core.items import Message, Reasoning, ToolCall, ToolResult, text_message
from harness.core.usage import Usage
from harness.env.local import LocalEnvironment
from harness.providers.base import (
    ItemDelta,
    ItemDone,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    StreamRetry,
    ToolSchema,
    UsageReport,
)
from harness.providers.gemini import (
    FOREIGN_SIGNATURE,
    GeminiProvider,
    GeminiStreamAssembler,
    build_body,
    to_contents,
)
from harness.redact import Redactor
from harness.store.jsonl import JsonlSessionStore
from harness.tools import default_tools
from openai_helpers import Script, ok

MODEL = "gemini-test"
USAGE = {
    "promptTokenCount": 100,
    "cachedContentTokenCount": 40,
    "candidatesTokenCount": 7,
    "thoughtsTokenCount": 20,
    "totalTokenCount": 127,
}

type Chunk = dict[str, Any]


def chunk(*parts: Chunk, finish: str | None = None, usage: Chunk | None = None) -> Chunk:
    candidate: Chunk = {"content": {"role": "model", "parts": list(parts)}, "index": 0}
    if finish is not None:
        candidate["finishReason"] = finish
    out: Chunk = {"candidates": [candidate]}
    if usage is not None:
        out["usageMetadata"] = usage
    return out


def sse(*chunks: Chunk) -> bytes:
    return "".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks).encode()


def fn(name: str, call_id: str | None = None, sig: str | None = None, **args: Any) -> Chunk:
    call: Chunk = {"name": name, "args": args}
    if call_id is not None:
        call["id"] = call_id
    part: Chunk = {"functionCall": call}
    if sig is not None:
        part["thoughtSignature"] = sig
    return part


def provider(script: Script, **kwargs: Any) -> GeminiProvider:
    options: dict[str, Any] = {
        "api_key": "g-key",
        "base_url": "https://example.test/v1beta",
        "transport": script.transport,
        "backoff_base_s": 0.0,
        "jitter": lambda: 1.0,
    }
    options.update(kwargs)
    return GeminiProvider(**options)


def request(*items: Any, tools: tuple[ToolSchema, ...] = ()) -> ModelRequest:
    return ModelRequest(model=MODEL, system="sys", tools=tools, items=items)


async def collect(source: GeminiProvider, req: ModelRequest) -> list[StreamEvent]:
    return [event async for event in source.stream(req, CancelToken())]


def done(events: list[StreamEvent]) -> list[Any]:
    return [e.item for e in events if isinstance(e, ItemDone)]


def feed_all(*chunks: Chunk) -> list[StreamEvent]:
    assembler = GeminiStreamAssembler(provider="gemini", model=MODEL)
    events = [event for c in chunks for event in assembler.feed(c)]
    return events + assembler.finish()


def test_thoughts_text_and_usage() -> None:
    events = feed_all(
        chunk({"text": "plan ", "thought": True}),
        chunk({"text": "it", "thought": True}),
        chunk({"text": "Hel"}),
        chunk({"text": "lo"}, finish="STOP", usage=USAGE),
    )
    assert [(e.index, e.text) for e in events if isinstance(e, ItemDelta)] == [
        (0, "plan "), (0, "it"), (1, "Hel"), (1, "lo")
    ]
    reasoning, message = done(events)
    assert isinstance(reasoning, Reasoning) and reasoning.summary_text == "plan it"
    assert isinstance(message, Message) and message.text == "Hello"
    usage = next(e for e in events if isinstance(e, UsageReport)).usage
    assert usage == Usage(input=60, cache_read=40, output=27, reasoning=20)
    assert events[-1] == StreamEnd(stop_reason="end_turn")


def test_parallel_calls_keep_the_first_signature() -> None:
    events = feed_all(
        chunk(fn("read", "c1", sig="SIG1", path="a"), fn("read", "c2", path="b")),
        chunk(finish="STOP"),
    )
    reasoning, first, second = done(events)
    assert isinstance(first, ToolCall) and isinstance(second, ToolCall)
    assert (first.provider_call_id, second.provider_call_id) == ("c1", "c2")
    assert first.arguments == {"path": "a"}
    assert isinstance(reasoning, Reasoning) and reasoning.payload == {"signatures": {"c1": "SIG1"}}
    assert events[-1] == StreamEnd(stop_reason="tool_use")


def test_round_trip_puts_signatures_back_on_their_parts() -> None:
    events = feed_all(
        chunk({"text": "thinking", "thought": True}),
        chunk(fn("read", "c1", sig="SIG1", path="a"), fn("bash", "c2", command="ls")),
        chunk(finish="STOP"),
    )
    reasoning, read, bash = done(events)
    results = (
        ToolResult(call_id=read.id, status="ok", content="alpha"),
        ToolResult(call_id=bash.id, status="error", content=""),
    )
    contents = to_contents(
        request(text_message("user", "go"), reasoning, read, bash, *results), provider="gemini"
    )
    assert contents == [
        {"role": "user", "parts": [{"text": "go"}]},
        {
            "role": "model",
            "parts": [
                {"functionCall": {"name": "read", "args": {"path": "a"}, "id": "c1"},
                 "thoughtSignature": "SIG1"},
                {"functionCall": {"name": "bash", "args": {"command": "ls"}, "id": "c2"}},
            ],
        },
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": "read", "response": {"output": "alpha"},
                                      "id": "c1"}},
                {"functionResponse": {"name": "bash",
                                      "response": {"error": "[error] (no output)"}, "id": "c2"}},
            ],
        },
    ]


def test_calls_without_ids_get_synthetic_ids_that_never_go_back() -> None:
    (call,) = done(feed_all(chunk(fn("glob", pattern="*")), chunk(finish="STOP")))
    assert call.provider_call_id.startswith("harness-call-")
    result = ToolResult(call_id=call.id, status="ok", content="x")
    contents = to_contents(request(text_message("user", "go"), call, result), provider="gemini")
    assert "id" not in contents[1]["parts"][0]["functionCall"]
    assert "id" not in contents[2]["parts"][0]["functionResponse"]


def test_text_signature_stays_in_its_own_turn() -> None:
    signed = feed_all(chunk({"text": "done", "thoughtSignature": "T1"}), chunk(finish="STOP"))
    bare = feed_all(chunk({"text": "", "thoughtSignature": "T2"}), chunk(finish="STOP"))
    contents = to_contents(
        request(
            text_message("user", "a"), *done(signed),
            text_message("user", "b"), *done(bare),
            text_message("user", "c"),
        ),
        provider="gemini",
    )
    assert contents[1] == {"role": "model", "parts": [{"text": "done", "thoughtSignature": "T1"}]}
    assert contents[3] == {"role": "model", "parts": [{"text": "", "thoughtSignature": "T2"}]}
    assert contents[4] == {"role": "user", "parts": [{"text": "c"}]}


def test_unsigned_calls_in_the_current_turn_get_the_skip_signature() -> None:
    old = ToolCall(id="o", provider_call_id="call_o", name="read", arguments={})
    new_a = ToolCall(id="a", provider_call_id="call_a", name="read", arguments={})
    new_b = ToolCall(id="b", provider_call_id="call_b", name="read", arguments={})
    foreign = Reasoning(provider="gemini", model_family="other-model",
                        payload={"signatures": {"call_a": "WRONG"}})
    contents = to_contents(
        request(
            text_message("user", "first"), old, ToolResult(call_id="o", status="ok", content="1"),
            text_message("assistant", "read it"),
            text_message("user", "second"), foreign, new_a, new_b,
            ToolResult(call_id="a", status="ok", content="2"),
            ToolResult(call_id="b", status="ok", content="3"),
        ),
        provider="gemini",
    )
    assert "thoughtSignature" not in contents[1]["parts"][0]
    assert contents[4] == {"role": "user", "parts": [{"text": "second"}]}
    current = contents[5]["parts"]
    assert current[0]["thoughtSignature"] == FOREIGN_SIGNATURE
    assert "thoughtSignature" not in current[1]


def test_body_shape() -> None:
    tool = ToolSchema(name="read", description="Read.", parameters={"type": "object"})
    body = build_body(
        ModelRequest(model=MODEL, system="be brief", tools=(tool,),
                     items=(text_message("user", "hi"),), max_output_tokens=64),
        provider="gemini",
        generation={"temperature": 0.2},
    )
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["tools"] == [{"functionDeclarations": [
        {"name": "read", "description": "Read.", "parametersJsonSchema": {"type": "object"}}
    ]}]
    assert body["generationConfig"] == {
        "thinkingConfig": {"includeThoughts": True}, "temperature": 0.2, "maxOutputTokens": 64
    }


def test_result_without_its_call_is_refused() -> None:
    with pytest.raises(ValueError, match="no matching call"):
        to_contents(request(ToolResult(call_id="x", status="ok", content="")), provider="gemini")


@pytest.mark.parametrize(
    ("finish", "stop"),
    [("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"), ("OTHER", "error")],
)
def test_finish_reasons(finish: str, stop: str) -> None:
    assert feed_all(chunk({"text": "x"}, finish=finish))[-1].stop_reason == stop  # type: ignore[union-attr]


def test_blocked_prompt_is_a_refusal() -> None:
    events = feed_all({"promptFeedback": {"blockReason": "SAFETY"}})
    assert events[-1] == StreamEnd(stop_reason="refusal", error="prompt blocked: SAFETY")


async def test_url_headers_and_a_resampled_malformed_call() -> None:
    script = Script([
        ok(sse(chunk(finish="MALFORMED_FUNCTION_CALL"))),
        ok(sse(chunk({"text": "ok"}, finish="STOP"))),
    ])
    events = await collect(provider(script), request(text_message("user", "hi")))
    assert isinstance(events[0], StreamRetry) and "MALFORMED_FUNCTION_CALL" in events[0].reason
    assert events[-1] == StreamEnd(stop_reason="end_turn")
    assert script.headers[0]["x-goog-api-key"] == "g-key"


async def test_cut_stream_is_retried_without_duplicates() -> None:
    script = Script(
        [ok(sse(chunk({"text": "par"}))), ok(sse(chunk({"text": "full"}, finish="STOP")))]
    )
    events = await collect(provider(script), request(text_message("user", "hi")))
    assert [i.text for i in done(events) if isinstance(i, Message)] == ["full"]


def rate_limited(delay: str) -> httpx.Response:
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                      "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                   "retryDelay": delay}]}}
    return httpx.Response(429, content=json.dumps(body).encode())


async def test_retry_delay_from_error_details() -> None:
    script = Script([rate_limited("2s"), ok(sse(chunk({"text": "ok"}, finish="STOP")))])
    events = await collect(provider(script), request(text_message("user", "hi")))
    retry = events[0]
    assert isinstance(retry, StreamRetry) and retry.delay_s >= 2.0 and retry.status == 429


async def test_a_per_minute_wait_over_the_backoff_cap_is_honored() -> None:
    script = Script([rate_limited("37s")])
    stream = provider(script).stream(request(text_message("user", "hi")), CancelToken())
    first = await anext(stream)
    await stream.aclose()
    assert isinstance(first, StreamRetry) and first.delay_s == 37.0


async def test_long_retry_delay_fails_fast() -> None:
    events = await collect(provider(Script([rate_limited("400s")])), request())
    assert events == [StreamEnd(
        stop_reason="error",
        error="HTTP 429: stream error 429: RESOURCE_EXHAUSTED quota "
        "(the server asks to wait 400s, over the 120s limit)",
    )]


async def test_signature_round_trips_through_the_log(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha\n")
    script = Script([
        ok(sse(chunk(fn("read", "c1", sig="SIG-ROUND", path="a.txt")), chunk(finish="STOP"))),
        ok(sse(chunk({"text": "alpha"}, finish="STOP", usage=USAGE))),
    ])
    store = JsonlSessionStore(tmp_path / "s", Redactor())
    with store.open("x", create=True) as log:
        agent = Agent(
            log=log,
            provider=provider(script),
            env=LocalEnvironment(tmp_path),
            tools=ToolRegistry(default_tools()),
            config=AgentConfig(model=MODEL, system_prompt="sys", mode="yolo"),
        )
        outcome = await agent.run("read a.txt")
        events = log.read()
    assert outcome.status == "done"
    second = script.bodies[1]["contents"]
    assert second[1]["parts"][0]["thoughtSignature"] == "SIG-ROUND"
    assert second[2]["parts"][0]["functionResponse"]["response"]["output"].endswith("alpha")
    started = [e.body for e in events if isinstance(e.body, ModelRequestStarted)]
    assert [s.reasoning_dropped for s in started] == [0, 0]


def test_a_prompt_merged_into_results_does_not_end_the_turn() -> None:
    foreign = ToolCall(id="f", provider_call_id="call_f", name="read", arguments={})
    own = ToolCall(id="g", provider_call_id="g1", name="edit", arguments={})
    signed = Reasoning(provider="gemini", model_family=MODEL, payload={"signatures": {"g1": "S"}})
    contents = to_contents(
        request(
            text_message("user", "fix it"), foreign,
            ToolResult(call_id="f", status="ok", content="code"),
            text_message("user", "continue"), signed, own,
            ToolResult(call_id="g", status="ok", content="edited"),
        ),
        provider="gemini",
    )
    assert [part.keys() for part in contents[2]["parts"]] == [
        {"functionResponse"}, {"text"}
    ]
    assert contents[1]["parts"][0]["thoughtSignature"] == FOREIGN_SIGNATURE
    assert contents[3]["parts"][0]["thoughtSignature"] == "S"
