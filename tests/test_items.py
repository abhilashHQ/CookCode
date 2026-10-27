import pytest

from cookcode.core.items import (
    ITEM_ADAPTER,
    ITEM_TYPES,
    Item,
    Message,
    OtherItem,
    Reasoning,
    ToolCall,
    ToolResult,
    UnknownItem,
    text_message,
)

SAMPLES: list[Item] = [
    text_message("user", "fix the bug"),
    Reasoning(provider="anthropic", model_family="claude-5", payload={"signature": "abc"}),
    ToolCall(id="c1", provider_call_id="call_1", provider_item_id="fc_1", name="bash",
             arguments={"cmd": "ls -la"}),
    ToolResult(call_id="c1", status="ok", content="file.py\n"),
    OtherItem(provider="openai", raw={"type": "web_search_call", "id": "ws_1"}),
]


def test_samples_cover_every_item_type() -> None:
    assert {item.type for item in SAMPLES} == ITEM_TYPES


@pytest.mark.parametrize("item", SAMPLES, ids=lambda i: i.type)
def test_round_trip_through_json(item: Item) -> None:
    restored = ITEM_ADAPTER.validate_json(ITEM_ADAPTER.dump_json(item))
    assert restored == item
    assert type(restored) is type(item)


def test_unknown_type_is_preserved_not_rejected() -> None:
    raw = b'{"type": "image_generation", "url": "x.png", "size": 3}'
    item = ITEM_ADAPTER.validate_json(raw)
    assert isinstance(item, UnknownItem)
    assert item.type == "image_generation"
    assert ITEM_ADAPTER.validate_json(ITEM_ADAPTER.dump_json(item)) == item


def test_message_text_joins_parts() -> None:
    message = text_message("assistant", "hello")
    assert isinstance(message, Message)
    assert message.text == "hello"


def test_reasoning_replays_only_to_same_provider_and_family() -> None:
    reasoning = Reasoning(provider="anthropic", model_family="claude-5", payload={})
    assert reasoning.replayable_to("anthropic", "claude-5")
    assert not reasoning.replayable_to("anthropic", "claude-4")
    assert not reasoning.replayable_to("openai", "claude-5")


def test_items_are_frozen() -> None:
    call = SAMPLES[2]
    with pytest.raises(ValueError):
        call.name = "other"  # type: ignore[misc]
