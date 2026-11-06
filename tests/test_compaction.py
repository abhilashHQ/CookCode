from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from agent_helpers import RigFactory, echo_tool
from cookcode.agent.context import (
    SUMMARIZE_VIEW,
    SUMMARY_HEADER,
    build_request,
    items_sha256,
    shared_prefix_tokens,
    summary_cut,
    token_ledger,
)
from cookcode.core.events import (
    CompactionApplied,
    Event,
    EventBody,
    ItemEvent,
    ModelRequestStarted,
    UserMessage,
    make_event,
)
from cookcode.core.items import Item, Message, ToolCall, ToolResult, text_message
from cookcode.core.strategy import Strategy
from cookcode.providers.base import ModelRequest, ToolSchema
from cookcode.providers.fake import FakeTurn, call, say
from cookcode.tools.recall import recall_tool

LONG = "x" * 300


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def session(steps: int, content: str = LONG) -> list[Event]:
    """A user message, then `steps` requests, each with one call and its result."""
    bodies: list[EventBody] = [UserMessage(text="task")]
    for step in range(steps):
        bodies.append(ModelRequestStarted(request_id=f"r{step}", view="raw", token_estimate=0))
        bodies.append(
            ItemEvent(
                item=ToolCall(id=f"c{step}", provider_call_id=f"p{step}", name="read", arguments={})
            )
        )
        bodies.append(ItemEvent(item=ToolResult(call_id=f"c{step}", status="ok", content=content)))
    return [make_event(seq, body) for seq, body in enumerate(bodies)]


def build(events: Sequence[Event], strategy: str) -> ModelRequest:
    return build_request(events, model="m", system="s", tools=(), strategy=Strategy.parse(strategy))


def masked_steps(items: Sequence[Item]) -> list[int]:
    return [
        int(item.call_id[1:])
        for item in items
        if isinstance(item, ToolResult) and item.content.startswith("[masked")
    ]


# --- strategy config --------------------------------------------------------------------


def test_strategy_labels_parse_back() -> None:
    labels = ("raw", "mask:k4", "mask_batched:k6:b8", "summarize:k2:t1000", "hybrid:k6:b8:t48000")
    for text in labels:
        assert Strategy.parse(text).label == text
    assert Strategy.parse("mask").keep_steps == 6
    with pytest.raises(ValueError, match="bad strategy option"):
        Strategy.parse("mask:z3")


# --- masking ----------------------------------------------------------------------------


def test_mask_replaces_outputs_older_than_keep_steps() -> None:
    request = build(session(4), "mask:k2")
    assert masked_steps(request.items) == [0, 1]
    first = next(i for i in request.items if isinstance(i, ToolResult))
    assert first.content == (
        '[masked to save context: 300 chars of read output. recall(call_id="p0") returns it.]'
    )
    assert first.status == "ok"
    assert build(session(4), "raw").items != request.items


def test_short_outputs_are_left_alone() -> None:
    assert masked_steps(build(session(5, "tiny"), "mask:k1").items) == []


def test_batched_masking_moves_in_jumps_and_keeps_the_prefix_between_them() -> None:
    masks = [masked_steps(build(session(n), "mask_batched:k1:b4").items) for n in range(1, 10)]
    assert masks == [[], [], [], [], [0, 1, 2, 3], [0, 1, 2, 3], [0, 1, 2, 3], [0, 1, 2, 3],
                     [0, 1, 2, 3, 4, 5, 6, 7]]
    five, six = build(session(5), "mask_batched:k1:b4"), build(session(6), "mask_batched:k1:b4")
    assert six.items[: len(five.items)] == five.items
    plain_five, plain_six = build(session(5), "mask:k1"), build(session(6), "mask:k1")
    assert plain_six.items[: len(plain_five.items)] != plain_five.items


# --- summaries --------------------------------------------------------------------------


def with_summary(events: list[Event], from_seq: int, to_seq: int) -> list[Event]:
    body = CompactionApplied(strategy="summarize:k1", from_seq=from_seq, to_seq=to_seq,
                             tokens_before=10, tokens_after=5, summary="did steps 0 and 1")
    return [*events, make_event(len(events), body)]


def test_a_logged_summary_replaces_its_range_for_summarizing_strategies_only() -> None:
    events = with_summary(session(3), 1, 6)
    items = build(events, "summarize:k1").items
    assert isinstance(items[0], Message) and items[0].text == "task"
    assert isinstance(items[1], Message) and items[1].text == f"{SUMMARY_HEADER}\ndid steps 0 and 1"
    assert [i.id for i in items if isinstance(i, ToolCall)] == ["c2"]
    assert build(events, "raw").items == build(session(3), "raw").items


def test_summary_cut_keeps_the_task_and_the_last_steps() -> None:
    events = session(3)
    assert summary_cut(events, 1) == (1, 6)
    assert summary_cut(events, 3) is None
    assert summary_cut(with_summary(events, 1, 6), 1) is None


# --- ledger and prefix ------------------------------------------------------------------


def test_ledger_splits_a_request_by_category() -> None:
    tool = ToolSchema(name="read", description="Read.", parameters={})
    request = ModelRequest(
        model="m",
        system="s" * 40,
        tools=(tool,),
        items=(
            text_message("user", "u" * 8),
            ToolCall(id="c", provider_call_id="p", name="read", arguments={"path": "abc"}),
            ToolResult(call_id="c", status="ok", content="o" * 400),
            text_message("assistant", "a" * 12),
        ),
    )
    ledger = token_ledger(request)
    assert ledger["system"] == 10 and ledger["user"] == 2 and ledger["tool_output"] == 100
    assert ledger["assistant"] == (12 + 4 + len('{"path": "abc"}')) // 4
    assert ledger["tools"] == len(tool.model_dump_json()) // 4 and ledger["reasoning"] == 0


def test_shared_prefix_counts_leading_identical_items() -> None:
    a, b = text_message("user", "a" * 40), text_message("user", "b" * 40)
    c = text_message("user", "c")
    assert shared_prefix_tokens([a, b], [a, b, c]) > shared_prefix_tokens([a, b], [a, c]) > 0
    assert shared_prefix_tokens([a], [b]) == 0


# --- in the loop ------------------------------------------------------------------------


def agent_requests(events: Sequence[Event]) -> list[tuple[int, ModelRequestStarted]]:
    return [
        (i, e.body)
        for i, e in enumerate(events)
        if isinstance(e.body, ModelRequestStarted) and e.body.view != SUMMARIZE_VIEW
    ]


def assert_replays(events: Sequence[Event], strategy: Strategy) -> None:
    for index, started in agent_requests(events):
        rebuilt = build_request(events[:index], model="fake-1", system="", tools=(),
                                provider="fake", strategy=strategy)
        assert started.items_sha256 == items_sha256(rebuilt.items)


async def test_masked_outputs_come_back_through_recall(rigs: RigFactory) -> None:
    first = call("echo", "p_first", text=LONG)
    script = [
        FakeTurn(items=(first,)),
        FakeTurn(items=(call("echo", text="b"),)),
        FakeTurn(items=(call("recall", call_id="p_first"),)),
        FakeTurn(items=(say("done"),)),
    ]
    strategy = Strategy.parse("mask:k1")
    rig = rigs.make(script, [echo_tool(), recall_tool()], strategy=strategy)
    outcome = await rig.agent.run("go")
    assert outcome.status == "done"
    third = rig.provider.requests[2]
    masked = [
        i for i in third.items if isinstance(i, ToolResult) and i.content.startswith("[masked")
    ]
    assert len(masked) == 1 and 'recall(call_id="p_first")' in masked[0].content
    results = [e.body.item for e in rig.events()
               if isinstance(e.body, ItemEvent) and isinstance(e.body.item, ToolResult)]
    assert results[-1].content == LONG
    started = [b for _, b in agent_requests(rig.events())]
    assert {b.view for b in started} == {"mask:k1"} and all(b.ledger for b in started)
    assert_replays(rig.events(), strategy)


async def test_a_masking_strategy_without_recall_is_refused(rigs: RigFactory) -> None:
    with pytest.raises(ValueError, match="needs a recall tool"):
        rigs.make([], [echo_tool()], strategy=Strategy.parse("mask"))


async def test_summarize_logs_the_summary_and_the_builder_uses_it(rigs: RigFactory) -> None:
    script = [
        FakeTurn(items=(call("echo", text=LONG),)),
        FakeTurn(items=(call("echo", text="second"),)),
        FakeTurn(items=(say("the agent echoed a long text, then 'second'"),)),
        FakeTurn(items=(say("done"),)),
    ]
    strategy = Strategy.parse("summarize:k1:t1")
    rig = rigs.make(script, [echo_tool()], strategy=strategy)
    outcome = await rig.agent.run("go")
    assert outcome.status == "done" and outcome.turns == 3
    summarizer, last = rig.provider.requests[2], rig.provider.requests[3]
    assert summarizer.tools == () and "[call echo]" in summarizer.items[0].model_dump_json()
    [applied] = [e.body for e in rig.events() if isinstance(e.body, CompactionApplied)]
    assert applied.summary == "the agent echoed a long text, then 'second'"
    assert applied.tokens_after < applied.tokens_before
    texts = [i.text for i in last.items if isinstance(i, Message)]
    assert texts[0] == "go" and texts[1].startswith(SUMMARY_HEADER)
    assert not any(isinstance(i, ToolResult) and i.content == LONG for i in last.items)
    logged = [e.body.item for e in rig.events() if isinstance(e.body, ItemEvent)]
    assert not any(isinstance(i, Message) and "echoed a long text" in i.text for i in logged)
    assert_replays(rig.events(), strategy)


# --- counterfactual ---------------------------------------------------------------------


def test_counterfactual_costs_compare_strategies_on_one_trajectory() -> None:
    from cookcode.evals.context import session_cost

    events = session(10)
    raw = session_cost(events, Strategy())
    masked = session_cost(events, Strategy.parse("mask:k2"))
    batched = session_cost(events, Strategy.parse("mask_batched:k2:b4"))
    assert raw.requests == masked.requests == 10 and raw.masked_requests == 0
    assert masked.input_tokens < raw.input_tokens and masked.masked_requests == 7
    assert raw.cacheable_share > batched.cacheable_share > masked.cacheable_share
    with pytest.raises(ValueError, match="needs model calls"):
        session_cost(events, Strategy.parse("summarize"))


def test_for_window_derives_the_trigger_only_when_unset() -> None:
    small = Strategy.parse("hybrid").for_window(16384)
    assert small.trigger_tokens == 8192 and small.label == "hybrid:k6:b8:t8192"
    assert Strategy.parse("hybrid:k6:b8:t40000").for_window(16384).trigger_tokens == 40000
    cloud = Strategy.parse("hybrid").for_window(1_000_000)
    assert cloud.label == Strategy.parse("hybrid").label == "hybrid:k6:b8:t48000"
    assert small.for_window(1_000_000).trigger_tokens == 8192
    for text in ("raw", "mask_batched:k6:b8"):
        assert Strategy.parse(text).for_window(4096).label == text


async def test_summary_fires_at_the_trigger_derived_from_the_window(rigs: RigFactory) -> None:
    script = [
        FakeTurn(items=(call("echo", text=LONG),)),
        FakeTurn(items=(call("echo", text="second"),)),
        FakeTurn(items=(say("a summary"),)),
        FakeTurn(items=(say("done"),)),
    ]
    tight = rigs.make(script, [echo_tool()], strategy=Strategy.parse("summarize:k1").for_window(4))
    assert (await tight.agent.run("go")).status == "done"
    assert any(isinstance(e.body, CompactionApplied) for e in tight.events())
    roomy = rigs.make(
        [script[0], script[1], script[3]], [echo_tool()], log=rigs.open_log("s2"),
        strategy=Strategy.parse("summarize:k1").for_window(1_000_000),
    )
    assert (await roomy.agent.run("go")).status == "done"
    assert not any(isinstance(e.body, CompactionApplied) for e in roomy.events())
