import pytest

from harness.core.cancel import CancelToken
from harness.core.items import text_message
from harness.core.usage import Usage
from harness.providers.base import (
    ItemDelta,
    ItemDone,
    ItemStarted,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    UsageReport,
)
from harness.providers.fake import FakeProvider, FakeScriptExhausted, FakeTurn, call, say

REQUEST = ModelRequest(model="fake-1", system="be brief", items=(text_message("user", "hi"),))


async def collect(provider: FakeProvider, cancel: CancelToken | None = None) -> list[StreamEvent]:
    return [event async for event in provider.stream(REQUEST, cancel or CancelToken())]


async def test_text_turn_streams_deltas_then_the_finished_item() -> None:
    fake = FakeProvider([FakeTurn(items=(say("hello there, world"),), usage=Usage(output=4))],
                        chunk_size=5)
    events = await collect(fake)
    assert isinstance(events[0], ItemStarted) and events[0].kind == "message"
    deltas = [e.text for e in events if isinstance(e, ItemDelta)]
    assert "".join(deltas) == "hello there, world"
    assert len(deltas) == 4
    done = [e for e in events if isinstance(e, ItemDone)]
    assert [d.item for d in done] == [say("hello there, world")]
    assert events[-2] == UsageReport(usage=Usage(output=4))
    assert events[-1] == StreamEnd(stop_reason="end_turn")


async def test_tool_call_turn_ends_with_tool_use() -> None:
    fake = FakeProvider([FakeTurn(items=(say("checking"), call("bash", cmd="ls")))])
    events = await collect(fake)
    done = [e.item for e in events if isinstance(e, ItemDone)]
    assert done[1].type == "tool_call"
    assert events[-1] == StreamEnd(stop_reason="tool_use")


async def test_requests_are_recorded_in_order() -> None:
    fake = FakeProvider([FakeTurn(items=(say("a"),)), FakeTurn(items=(say("b"),))])
    await collect(fake)
    await collect(fake)
    assert fake.requests == [REQUEST, REQUEST]


async def test_running_past_the_script_raises() -> None:
    fake = FakeProvider([FakeTurn(items=(say("only"),))])
    await collect(fake)
    with pytest.raises(FakeScriptExhausted):
        await collect(fake)


async def test_scripted_failure_ends_with_error() -> None:
    fake = FakeProvider([FakeTurn(items=(), fail_with="overloaded")])
    assert await collect(fake) == [StreamEnd(stop_reason="error", error="overloaded")]


async def test_cancel_mid_stream_keeps_only_finished_items() -> None:
    fake = FakeProvider([FakeTurn(items=(say("first"), say("second")))], event_delay_s=0.01)
    cancel = CancelToken()
    seen: list[StreamEvent] = []
    async for event in fake.stream(REQUEST, cancel):
        seen.append(event)
        if isinstance(event, ItemDone):
            cancel.cancel()
    assert [e.item for e in seen if isinstance(e, ItemDone)] == [say("first")]
    assert seen[-1] == StreamEnd(stop_reason="aborted")


async def test_cancel_before_start_aborts_immediately() -> None:
    fake = FakeProvider([FakeTurn(items=(say("never"),))])
    cancel = CancelToken()
    cancel.cancel()
    assert await collect(fake, cancel) == [StreamEnd(stop_reason="aborted")]


def test_call_helper_builds_distinct_ids() -> None:
    a, b = call("read", path="x.py"), call("read", path="x.py")
    assert a.id != b.id
    assert a.provider_call_id != b.provider_call_id
    assert a.arguments == {"path": "x.py"}


@pytest.mark.parametrize(
    "turn", [FakeTurn(items=()), FakeTurn(items=(), fail_with="boom"), FakeTurn(items=(say("x"),))]
)
async def test_precancelled_stream_aborts_without_consuming_the_turn(turn: FakeTurn) -> None:
    fake = FakeProvider([turn])
    token = CancelToken()
    token.cancel("user")
    assert await collect(fake, token) == [StreamEnd(stop_reason="aborted")]
    assert (await collect(fake))[-1].stop_reason != "aborted"  # pyright: ignore[reportAttributeAccessIssue]


async def test_cancel_after_the_last_item_still_aborts() -> None:
    fake = FakeProvider([FakeTurn(items=(say("x"),))])
    token = CancelToken()
    events: list[StreamEvent] = []
    async for event in fake.stream(REQUEST, token):
        events.append(event)
        if isinstance(event, ItemDone):
            token.cancel("user")
    assert isinstance(events[-2], ItemDone)
    assert events[-1] == StreamEnd(stop_reason="aborted")
