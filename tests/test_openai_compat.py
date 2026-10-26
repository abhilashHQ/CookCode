import asyncio

import httpx
import pytest

from harness.core.cancel import CancelToken
from harness.core.items import (
    Message,
    OtherItem,
    Reasoning,
    ToolCall,
    ToolResult,
    text_message,
)
from harness.core.usage import Usage
from harness.providers.base import (
    ItemDelta,
    ItemDone,
    ItemStarted,
    ModelRequest,
    StreamEnd,
    StreamRetry,
    ToolSchema,
    UsageReport,
)
from harness.providers.openai_compat import (
    ChatStreamAssembler,
    SSEDecoder,
    StreamFailure,
    build_body,
    to_messages,
)
from openai_helpers import (
    MODEL,
    Script,
    SlowStream,
    call_delta,
    collect,
    delta,
    ok,
    provider,
    sse,
    status,
    text_stream,
)

CALL_A = ToolCall(id="ours-a", provider_call_id="call_a", name="read", arguments={"path": "a"})
CALL_B = ToolCall(id="ours-b", provider_call_id="call_b", name="read", arguments={"path": "b"})
THOUGHT = Reasoning(
    provider="openrouter",
    model_family=MODEL,
    payload={"reasoning_details": [{"type": "reasoning.text", "text": "plan", "index": 0}]},
)


def request(*items: object, **kwargs: object) -> ModelRequest:
    return ModelRequest(model=MODEL, system="sys", items=items, **kwargs)  # pyright: ignore[reportArgumentType]


# --- request mapping -------------------------------------------------------------------


def test_one_response_becomes_one_assistant_message_with_its_results_after() -> None:
    messages = to_messages(
        request(
            text_message("user", "go"),
            THOUGHT,
            text_message("assistant", "reading both"),
            CALL_A,
            CALL_B,
            ToolResult(call_id="ours-a", status="ok", content="alpha"),
            ToolResult(call_id="ours-b", status="ok", content="beta"),
        ),
        provider="openrouter",
    )
    assert messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "reading both",
            "tool_calls": [
                {"id": "call_a", "type": "function",
                 "function": {"name": "read", "arguments": '{"path": "a"}'}},
                {"id": "call_b", "type": "function",
                 "function": {"name": "read", "arguments": '{"path": "b"}'}},
            ],
            "reasoning_details": [{"type": "reasoning.text", "text": "plan", "index": 0}],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "alpha"},
        {"role": "tool", "tool_call_id": "call_b", "content": "beta"},
    ]


def test_assistant_with_calls_and_no_text_sends_null_content() -> None:
    messages = to_messages(
        request(CALL_A, ToolResult(call_id="ours-a", status="ok", content="x")),
        provider="openrouter",
    )
    assert messages[1]["content"] is None


@pytest.mark.parametrize("status_", ["error", "denied", "interrupted", "unknown"])
def test_non_ok_results_carry_their_status_in_the_text(status_: str) -> None:
    result = ToolResult(call_id="ours-a", status=status_, content="why")  # pyright: ignore[reportArgumentType]
    messages = to_messages(request(CALL_A, result), provider="openrouter")
    assert messages[-1]["content"] == f"[{status_}] why"


def test_reasoning_from_another_model_or_provider_is_not_replayed() -> None:
    other_model = THOUGHT.model_copy(update={"model_family": "vendor/model-b"})
    other_provider = THOUGHT.model_copy(update={"provider": "gemini"})
    for reasoning in (other_model, other_provider):
        messages = to_messages(
            request(reasoning, text_message("assistant", "done")), provider="openrouter"
        )
        assert messages[-1] == {"role": "assistant", "content": "done"}


def test_reasoning_only_turn_still_sends_a_valid_assistant_message() -> None:
    messages = to_messages(request(THOUGHT), provider="openrouter")
    assert messages[-1]["content"] == ""
    assert messages[-1]["reasoning_details"]


def test_plain_reasoning_text_is_replayed_when_there_are_no_details() -> None:
    thought = Reasoning(provider="openrouter", model_family=MODEL, payload={"reasoning": "hmm"})
    messages = to_messages(request(thought, CALL_A), provider="openrouter")
    assert messages[-1]["reasoning"] == "hmm"


def test_unparseable_arguments_go_back_verbatim() -> None:
    bad = CALL_A.model_copy(update={"arguments": {}, "raw_arguments": '{"path": "a"'})
    messages = to_messages(request(bad), provider="openrouter")
    assert messages[-1]["tool_calls"][0]["function"]["arguments"] == '{"path": "a"'


def test_other_items_replay_only_to_their_own_provider() -> None:
    mine = OtherItem(provider="openrouter", raw={"role": "assistant", "content": "raw"})
    theirs = OtherItem(provider="gemini", raw={"role": "model"})
    assert to_messages(request(mine, theirs), provider="openrouter")[1:] == [
        {"role": "assistant", "content": "raw"}
    ]


def test_a_result_without_its_call_is_refused() -> None:
    with pytest.raises(ValueError, match="no matching call"):
        to_messages(
            request(ToolResult(call_id="ghost", status="ok", content="")), provider="openrouter"
        )


def test_body_carries_tools_streaming_and_limits() -> None:
    schema = ToolSchema(name="read", description="Read a file", parameters={"type": "object"})
    body = build_body(
        request(text_message("user", "hi"), tools=(schema,), max_output_tokens=512),
        provider="openrouter",
        extra={"reasoning": {"effort": "low"}},
    )
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 512
    assert body["reasoning"] == {"effort": "low"}
    assert body["tools"] == [
        {"type": "function",
         "function": {"name": "read", "description": "Read a file",
                      "parameters": {"type": "object"}}}
    ]


def test_body_omits_tools_when_there_are_none() -> None:
    assert "tools" not in build_body(request(text_message("user", "hi")), provider="openrouter")


# --- SSE and assembly -------------------------------------------------------------------


def test_sse_decoder_skips_comments_and_joins_multiline_data() -> None:
    decoder = SSEDecoder()
    out = [decoder.feed(line) for line in
           [": OPENROUTER PROCESSING", "", "event: x", "data: {\"a\":", "data: 1}", ""]]
    assert [o for o in out if o is not None] == ['{"a":\n1}']
    assert decoder.feed("data: tail") is None
    assert decoder.flush() == "tail"
    assert decoder.flush() is None


def assemble(*chunks: dict[str, object]) -> list[object]:
    assembler = ChatStreamAssembler(provider="openrouter", model=MODEL)
    events: list[object] = []
    for chunk in chunks:
        events.extend(assembler.feed(chunk))
    events.extend(assembler.finish())
    return events


def test_parallel_fragmented_tool_calls_assemble_in_order() -> None:
    events = assemble(
        call_delta(0, id="c0", name="read", arguments=""),
        call_delta(0, arguments='{"pa'),
        call_delta(1, id="c1", name="read", arguments='{"path": "b"}'),
        call_delta(0, arguments='th": "a"}'),
        delta("tool_calls"),
    )
    done = [e.item for e in events if isinstance(e, ItemDone)]
    assert [(c.provider_call_id, c.arguments) for c in done if isinstance(c, ToolCall)] == [
        ("c0", {"path": "a"}),
        ("c1", {"path": "b"}),
    ]
    assert len({c.id for c in done if isinstance(c, ToolCall)}) == 2
    assert events[-1] == StreamEnd(stop_reason="tool_use")


def test_reasoning_text_and_call_arrive_as_three_items_once_the_response_ends() -> None:
    events = assemble(
        delta(reasoning="Think", reasoning_details=[
            {"type": "reasoning.text", "text": "Think", "index": 0, "format": "unknown"}]),
        delta(reasoning="ing", reasoning_details=[
            {"type": "reasoning.text", "text": "ing", "index": 0, "format": "unknown"}]),
        delta(content="Hello"),
        call_delta(0, id="c0", name="bash", arguments='{"command": "ls"}'),
        delta("tool_calls"),
    )
    started = [e for e in events if isinstance(e, ItemStarted)]
    assert [(s.index, s.kind) for s in started] == [
        (0, "reasoning"), (1, "message"), (2, "tool_call")
    ]
    deltas = [e for e in events if isinstance(e, ItemDelta)]
    assert [(d.index, d.text) for d in deltas] == [(0, "Think"), (0, "ing"), (1, "Hello")]
    first_done = next(i for i, e in enumerate(events) if isinstance(e, ItemDone))
    assert all(not isinstance(e, ItemDelta) for e in events[first_done:])
    items = [e.item for e in events if isinstance(e, ItemDone)]
    reasoning, message, tool_call = items
    assert isinstance(reasoning, Reasoning)
    assert reasoning.payload == {
        "reasoning_details": [
            {"type": "reasoning.text", "text": "Thinking", "index": 0, "format": "unknown"}
        ],
        "reasoning": "Thinking",
    }
    assert reasoning.summary_text == "Thinking"
    assert reasoning.replayable_to("openrouter", MODEL)
    assert message == text_message("assistant", "Hello")
    assert isinstance(tool_call, ToolCall) and tool_call.name == "bash"


def test_usage_splits_cached_tokens_out_of_the_prompt() -> None:
    raw = {
        "prompt_tokens": 1000,
        "completion_tokens": 300,
        "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100},
        "completion_tokens_details": {"reasoning_tokens": 250},
        "cost": 0,
    }
    events = assemble(delta(content="x"), delta("stop"), delta(usage=raw))
    report = next(e for e in events if isinstance(e, UsageReport))
    assert report.usage == Usage(input=300, cache_read=600, cache_write=100, output=300,
                                 reasoning=250)
    assert report.raw == raw


@pytest.mark.parametrize(
    ("finish", "stop"),
    [("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "refusal"),
     ("something_new", "end_turn")],
)
def test_finish_reasons_normalize(finish: str, stop: str) -> None:
    assert assemble(delta(content="x"), delta(finish))[-1] == StreamEnd(stop_reason=stop)  # pyright: ignore[reportArgumentType]


def test_a_stop_finish_with_tool_calls_still_reports_tool_use() -> None:
    events = assemble(call_delta(0, id="c", name="read", arguments="{}"), delta("stop"))
    assert events[-1] == StreamEnd(stop_reason="tool_use")


@pytest.mark.parametrize("arguments", ['{"path": ', "[1, 2]", '"text"'])
def test_arguments_that_are_not_a_json_object_are_kept_raw(arguments: str) -> None:
    events = assemble(call_delta(0, id="c", name="read", arguments=arguments), delta("tool_calls"))
    call_item = next(e.item for e in events if isinstance(e, ItemDone))
    assert isinstance(call_item, ToolCall)
    assert call_item.arguments == {}
    assert call_item.raw_arguments == arguments


def test_empty_arguments_mean_no_arguments() -> None:
    events = assemble(call_delta(0, id="c", name="list", arguments=""), delta("tool_calls"))
    call_item = next(e.item for e in events if isinstance(e, ItemDone))
    assert isinstance(call_item, ToolCall)
    assert call_item.arguments == {} and call_item.raw_arguments is None


def test_a_call_without_an_id_gets_one_so_its_result_can_match() -> None:
    events = assemble(call_delta(0, name="read", arguments="{}"), delta("tool_calls"))
    call_item = next(e.item for e in events if isinstance(e, ItemDone))
    assert isinstance(call_item, ToolCall) and call_item.provider_call_id.startswith("call_")


def test_a_stream_without_a_finish_reason_is_a_retryable_failure() -> None:
    assembler = ChatStreamAssembler(provider="openrouter", model=MODEL)
    assembler.feed(delta(content="partial"))
    with pytest.raises(StreamFailure) as raised:
        assembler.finish()
    assert raised.value.retryable


@pytest.mark.parametrize(("code", "retryable"), [(502, True), (429, True), (400, False)])
def test_error_chunks_fail_by_code(code: int, retryable: bool) -> None:
    assembler = ChatStreamAssembler(provider="openrouter", model=MODEL)
    with pytest.raises(StreamFailure) as raised:
        assembler.feed({"error": {"code": code, "message": "upstream"}})
    assert raised.value.retryable is retryable
    assert raised.value.status == code


# --- provider over HTTP -----------------------------------------------------------------


async def test_stream_posts_the_body_with_auth_and_yields_items() -> None:
    script = Script([ok(text_stream("hi there"))])
    source = provider(script)
    req = request(text_message("user", "hello"))
    events = await collect(source, req)
    assert script.bodies == [source.build_body(req)]
    assert script.headers[0]["authorization"] == "Bearer test-key"
    assert [e.item for e in events if isinstance(e, ItemDone)] == [
        text_message("assistant", "hi there")
    ]
    assert events[-1] == StreamEnd(stop_reason="end_turn")


async def test_a_rate_limit_is_retried_and_reported() -> None:
    script = Script([status(429, "slow down", {"retry-after": "0"}), ok(text_stream("ok"))])
    events = await collect(provider(script), request(text_message("user", "x")))
    retries = [e for e in events if isinstance(e, StreamRetry)]
    assert len(retries) == 1
    assert retries[0].attempt == 1 and retries[0].status == 429
    assert "slow down" in retries[0].reason
    assert events[-1] == StreamEnd(stop_reason="end_turn")
    assert len(script.bodies) == 2


async def test_retry_after_raises_the_delay_up_to_the_cap() -> None:
    script = Script([status(503, headers={"retry-after": "7"}), ok(text_stream("ok"))])
    events = await collect(
        provider(script, backoff_base_s=0.0, backoff_cap_s=0.01), request(text_message("user", "x"))
    )
    assert next(e for e in events if isinstance(e, StreamRetry)).delay_s == pytest.approx(0.01)


@pytest.mark.parametrize(
    "failure",
    [
        lambda: ok(sse([delta(content="half")], done=False)),
        lambda: ok(sse([delta(content="a"), {"error": {"code": 502, "message": "upstream"}}])),
        lambda: ok(b"data: {not json\n\n"),
    ],
    ids=["truncated", "error-chunk", "malformed"],
)
async def test_failures_before_the_response_ends_are_retried(failure: object) -> None:
    script = Script([failure, ok(text_stream("whole"))])  # pyright: ignore[reportArgumentType]
    events = await collect(provider(script), request(text_message("user", "x")))
    assert sum(isinstance(e, StreamRetry) for e in events) == 1
    assert [e.item for e in events if isinstance(e, ItemDone)] == [
        text_message("assistant", "whole")
    ]


async def test_transport_errors_are_retried() -> None:
    def boom() -> httpx.Response:
        raise httpx.ConnectError("refused")

    script = Script([boom, ok(text_stream("ok"))])
    events = await collect(provider(script), request(text_message("user", "x")))
    assert "ConnectError" in next(e for e in events if isinstance(e, StreamRetry)).reason


async def test_a_client_error_ends_the_stream_without_retrying() -> None:
    script = Script([status(400, "tool_calls must be followed by tool messages")])
    events = await collect(provider(script), request(text_message("user", "x")))
    assert events == [
        StreamEnd(stop_reason="error",
                  error="HTTP 400: stream error 400: tool_calls must be followed by tool messages")
    ]


async def test_retries_stop_at_the_cap() -> None:
    script = Script([status(500), status(500), status(500)])
    events = await collect(provider(script, max_retries=2), request(text_message("user", "x")))
    assert sum(isinstance(e, StreamRetry) for e in events) == 2
    last = events[-1]
    assert isinstance(last, StreamEnd) and last.stop_reason == "error"
    assert last.error is not None and last.error.endswith("after 3 attempts")


async def test_cancel_mid_stream_aborts_without_items_and_closes_the_response() -> None:
    started = asyncio.Event()
    body = SlowStream(sse([delta(content="partial")], done=False), started)
    script = Script([httpx.Response(200, stream=body)])
    cancel = CancelToken()
    source = provider(script)

    async def stop() -> None:
        await started.wait()
        cancel.cancel()

    stopper = asyncio.create_task(stop())
    async with asyncio.timeout(5):
        events = await collect(source, request(text_message("user", "x")), cancel)
    await stopper
    assert events[-1] == StreamEnd(stop_reason="aborted")
    assert not any(isinstance(e, ItemDone) for e in events)
    assert [e.text for e in events if isinstance(e, ItemDelta)] == ["partial"]
    assert body.closed


async def test_cancel_during_backoff_aborts() -> None:
    script = Script([status(503)])
    cancel = CancelToken()
    source = provider(script, backoff_base_s=30.0, backoff_cap_s=30.0)
    events: list[object] = []
    async with asyncio.timeout(5):
        async for event in source.stream(request(text_message("user", "x")), cancel):
            events.append(event)
            if isinstance(event, StreamRetry):
                cancel.cancel()
    assert events[-1] == StreamEnd(stop_reason="aborted")


async def test_an_already_cancelled_token_makes_no_request() -> None:
    script = Script([])
    cancel = CancelToken()
    cancel.cancel()
    events = await collect(provider(script), request(text_message("user", "x")), cancel)
    assert events == [StreamEnd(stop_reason="aborted")]
    assert script.bodies == []


def test_items_from_the_assembler_validate_as_the_neutral_model() -> None:
    events = assemble(delta(content="x"), delta("stop"))
    assert isinstance(next(e.item for e in events if isinstance(e, ItemDone)), Message)
