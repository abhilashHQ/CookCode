from collections.abc import Sequence

from harness.agent.context import build_request, estimate_tokens
from harness.core.events import (
    CheckpointRestored,
    Event,
    EventBody,
    ItemEvent,
    ModelRequestStarted,
    RunConfig,
    RunStarted,
    SteerMessage,
    ToolCallStarted,
    UserMessage,
    make_event,
)
from harness.core.items import Item, ToolCall, ToolResult, text_message
from harness.providers.base import ToolSchema
from harness.providers.fake import call, say

TOOLS = (ToolSchema(name="read", description="Read.", parameters={"type": "object"}),)


def _events(*bodies: EventBody) -> list[Event]:
    return [make_event(seq, body) for seq, body in enumerate(bodies)]


def _build(events: Sequence[Event]):
    return build_request(events, model="m", system="sys", tools=TOOLS)


def _call(call_id: str) -> ItemEvent:
    return ItemEvent(
        item=ToolCall(id=call_id, provider_call_id=f"p_{call_id}", name="read", arguments={})
    )


def _result(call_id: str, content: str = "") -> ItemEvent:
    return ItemEvent(item=ToolResult(call_id=call_id, status="ok", content=content or call_id))


def _result_ids(items: Sequence[Item]) -> list[str]:
    return [item.call_id for item in items if isinstance(item, ToolResult)]


def test_empty_log_gives_empty_request() -> None:
    request = build_request([], model="m", system="sys", tools=TOOLS, max_output_tokens=123)
    assert request.items == ()
    assert request.model == "m"
    assert request.system == "sys"
    assert request.tools == TOOLS
    assert request.max_output_tokens == 123


def test_only_model_layer_events_appear_in_order() -> None:
    answer = text_message("assistant", "ok")
    events = _events(
        UserMessage(text="fix it"),
        ModelRequestStarted(request_id="r1", view="raw", token_estimate=10),
        ItemEvent(item=answer),
        SteerMessage(text="also lint"),
        ToolCallStarted(call_id="c", tool="read", arguments={}, side_effect="read_only"),
    )
    request = _build(events)
    assert request.items == (
        text_message("user", "fix it"),
        answer,
        text_message("user", "also lint"),
    )


def test_results_are_reordered_into_call_order() -> None:
    events = _events(_call("A"), _call("B"), _call("C"), _result("C"), _result("A"), _result("B"))
    assert _result_ids(_build(events).items) == ["A", "B", "C"]


def test_each_step_is_reordered_against_its_own_calls() -> None:
    events = _events(
        _call("A"),
        _call("B"),
        _result("B"),
        _result("A"),
        ItemEvent(item=text_message("assistant", "next")),
        _call("C"),
        _call("D"),
        _result("D"),
        _result("C"),
    )
    assert _result_ids(_build(events).items) == ["A", "B", "C", "D"]


def test_unmatched_result_stays_after_matched_ones() -> None:
    events = _events(_call("A"), _call("B"), _result("X"), _result("B"), _result("A"))
    assert _result_ids(_build(events).items) == ["A", "B", "X"]


def test_unmatched_results_keep_relative_order() -> None:
    events = _events(_call("A"), _result("Y"), _result("X"), _result("A"))
    assert _result_ids(_build(events).items) == ["A", "Y", "X"]


def test_non_result_items_between_runs_are_not_moved() -> None:
    events = _events(_call("A"), _call("B"), _result("B"), _result("A"), UserMessage(text="hm"))
    items = _build(events).items
    assert items[-1] == text_message("user", "hm")
    assert _result_ids(items) == ["A", "B"]


def test_build_is_pure() -> None:
    events = _events(UserMessage(text="x"), _call("A"), _call("B"), _result("B"), _result("A"))
    snapshot = list(events)
    first = _build(events)
    second = _build(events)
    assert first == second
    assert events == snapshot


def test_estimate_tokens_is_positive_and_grows() -> None:
    small = _build(_events(UserMessage(text="x")))
    larger = _build(
        _events(UserMessage(text="x"), ItemEvent(item=text_message("assistant", "y" * 400)))
    )
    assert estimate_tokens(small) > 0
    assert estimate_tokens(larger) > estimate_tokens(small)
    assert estimate_tokens(small) == len(small.model_dump_json()) // 4


def test_text_between_calls_does_not_split_the_response() -> None:
    a, b = call("echo", text="a"), call("echo", text="b")
    events = _events(
        UserMessage(text="go"),
        ItemEvent(item=a),
        ItemEvent(item=say("now the second one")),
        ItemEvent(item=b),
        ItemEvent(item=ToolResult(call_id=b.id, status="ok", content="b")),
        ItemEvent(item=ToolResult(call_id=a.id, status="ok", content="a")),
    )
    request = build_request(events, model="m", system="s", tools=())
    result_ids = [i.call_id for i in request.items if isinstance(i, ToolResult)]
    assert result_ids == [a.id, b.id]


def test_checkpoint_restored_renders_as_an_undo_message() -> None:
    request = _build(
        _events(
            UserMessage(text="go"),
            CheckpointRestored(checkpoint_ids=("c1", "c2"), paths=("a.py", "b.py"), turns=(3, 4)),
        )
    )
    assert request.items[-1] == text_message(
        "user",
        "[undo] Restored a.py, b.py to their state before turn 3. "
        "File edits made in turns 3, 4 are gone.",
    )


def test_checkpoint_restored_uses_the_earliest_turn_and_sorts_unique_turns() -> None:
    request = _build(
        _events(
            CheckpointRestored(checkpoint_ids=("c1", "c2", "c3"), paths=("a",), turns=(5, 2, 5))
        )
    )
    assert request.items[-1] == text_message(
        "user",
        "[undo] Restored a to their state before turn 2. File edits made in turns 2, 5 are gone.",
    )


def test_run_started_is_not_shown_to_the_model() -> None:
    config = RunConfig(provider="fake", model="m", harness_version="0.0.1")
    request = _build(_events(RunStarted.for_config(config), UserMessage(text="go")))
    assert request.items == (text_message("user", "go"),)
