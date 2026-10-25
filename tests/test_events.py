import pytest
from pydantic import ValidationError

from harness.core.events import (
    EVENT_TYPES,
    SCHEMA_VERSION,
    Event,
    EventBody,
    ItemEvent,
    OtherEvent,
    RunConfig,
    SessionStarted,
    UserMessage,
    compute_config_id,
    from_json_line,
    make_event,
    to_json_line,
)
from harness.core.items import text_message
from sample_events import all_bodies


def test_samples_cover_every_event_type() -> None:
    assert {body.type for body in all_bodies()} == EVENT_TYPES


@pytest.mark.parametrize("body", all_bodies(), ids=lambda b: b.type)
def test_every_event_round_trips_through_a_json_line(body: EventBody) -> None:
    event = make_event(7, body)
    line = to_json_line(event)
    assert "\n" not in line
    restored = from_json_line(line)
    assert restored == event
    assert type(restored.body) is type(body)
    assert restored.schema_version == SCHEMA_VERSION


def test_layer_comes_from_the_body_type() -> None:
    assert make_event(0, UserMessage(text="hi")).layer == "model"
    assert make_event(0, ItemEvent(item=text_message("assistant", "ok"))).layer == "model"
    config = RunConfig(provider="fake", model="m", harness_version="0.0.1")
    assert make_event(0, SessionStarted.for_config(config)).layer == "internal"


def test_layer_mismatch_is_rejected() -> None:
    event = make_event(0, UserMessage(text="hi"))
    data = event.model_dump(mode="json")
    data["layer"] = "internal"
    with pytest.raises(ValidationError):
        Event.model_validate(data)


def test_unknown_event_type_from_a_newer_schema_is_preserved() -> None:
    line = (
        '{"seq": 3, "id": "e3", "ts": "2026-10-26T10:00:00Z", "schema_version": 2,'
        ' "layer": "internal", "body": {"type": "lease_renewed", "worker": "w1", "ttl_s": 30}}'
    )
    event = from_json_line(line)
    assert isinstance(event.body, OtherEvent)
    assert event.body.type == "lease_renewed"
    assert from_json_line(to_json_line(event)) == event


def test_config_id_is_stable_and_sensitive_to_changes() -> None:
    a = RunConfig(provider="fake", model="m1", harness_version="0.0.1")
    b = RunConfig(provider="fake", model="m1", harness_version="0.0.1")
    c = RunConfig(provider="fake", model="m2", harness_version="0.0.1")
    assert compute_config_id(a) == compute_config_id(b)
    assert compute_config_id(a) != compute_config_id(c)
    assert len(compute_config_id(a)) == 16
    assert SessionStarted.for_config(a).config_id == compute_config_id(a)


def test_negative_seq_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_event(-1, UserMessage(text="hi"))
