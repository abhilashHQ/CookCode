import inspect

import pytest

from harness.agent.repair import RepairAction, plan_repairs, repair_bodies, unresolved_calls
from harness.core.events import (
    Event,
    EventBody,
    ItemEvent,
    Repaired,
    SideEffect,
    ToolCallFinished,
    ToolCallStarted,
    UserMessage,
    from_json_line,
    make_event,
    to_json_line,
)
from harness.core.items import ToolCall, ToolResult

NOT_EXECUTED = "not executed: the session stopped before this call ran. Safe to issue again."
READ_ONLY_LOST = (
    "interrupted: the call started but its outcome was not recorded. "
    "It is read-only, so it is safe to retry."
)
MUTATING_LOST = (
    "unknown: the call started and may have changed state before the session stopped. "
    "Inspect before retrying."
)


def _events(*bodies: EventBody) -> list[Event]:
    return [make_event(seq, body) for seq, body in enumerate(bodies)]


def _call(call_id: str, tool: str = "t") -> ItemEvent:
    return ItemEvent(
        item=ToolCall(id=call_id, provider_call_id=f"p_{call_id}", name=tool, arguments={})
    )


def _started(call_id: str, side_effect: SideEffect, tool: str = "t") -> ToolCallStarted:
    return ToolCallStarted(call_id=call_id, tool=tool, arguments={}, side_effect=side_effect)


def _finished(call_id: str, exit_status: str = "ok") -> ToolCallFinished:
    return ToolCallFinished.model_validate(
        {
            "call_id": call_id,
            "exit_status": exit_status,
            "duration_s": 0.1,
            "output_bytes": 3,
            "truncated": False,
        }
    )


def _result(call_id: str) -> ItemEvent:
    return ItemEvent(item=ToolResult(call_id=call_id, status="ok", content="done"))


def test_call_that_never_started_is_interrupted_not_executed() -> None:
    actions = plan_repairs(_events(UserMessage(text="go"), _call("c1", "bash")))
    assert actions == [
        RepairAction(call_id="c1", tool="bash", status="interrupted", reason=NOT_EXECUTED)
    ]


def test_started_read_only_call_without_result_is_interrupted() -> None:
    actions = plan_repairs(_events(_call("c1", "read"), _started("c1", "read_only", "read")))
    assert actions == [
        RepairAction(call_id="c1", tool="read", status="interrupted", reason=READ_ONLY_LOST)
    ]


@pytest.mark.parametrize("side_effect", ["mutating", "external"])
def test_started_mutating_or_external_call_without_result_is_unknown(
    side_effect: SideEffect,
) -> None:
    actions = plan_repairs(_events(_call("c1", "bash"), _started("c1", side_effect, "bash")))
    assert actions == [
        RepairAction(call_id="c1", tool="bash", status="unknown", reason=MUTATING_LOST)
    ]


def test_finished_but_result_lost_read_only_appends_exit_status() -> None:
    events = _events(_call("c1"), _started("c1", "read_only"), _finished("c1", "ok"))
    [action] = plan_repairs(events)
    assert action.status == "interrupted"
    assert action.reason == (
        READ_ONLY_LOST + " It finished with exit status ok, but its result was lost."
    )


def test_finished_but_result_lost_mutating_appends_exit_status() -> None:
    events = _events(_call("c1"), _started("c1", "mutating"), _finished("c1", "timeout"))
    [action] = plan_repairs(events)
    assert action.status == "unknown"
    assert action.reason == (
        MUTATING_LOST + " It finished with exit status timeout, but its result was lost."
    )


def test_fully_resolved_log_needs_no_repair() -> None:
    events = _events(_call("c1"), _started("c1", "mutating"), _finished("c1"), _result("c1"))
    assert plan_repairs(events) == []
    assert unresolved_calls(events) == []


def test_mixed_step_yields_actions_in_log_order() -> None:
    events = _events(
        _call("a"),
        _call("b"),
        _call("c"),
        _started("a", "read_only"),
        _started("b", "mutating"),
        _finished("a"),
        _result("a"),
    )
    actions = plan_repairs(events)
    assert [(a.call_id, a.status) for a in actions] == [("b", "unknown"), ("c", "interrupted")]
    assert [call.id for call in unresolved_calls(events)] == ["b", "c"]


def test_side_effect_comes_from_the_log_and_planner_takes_only_events() -> None:
    assert list(inspect.signature(plan_repairs).parameters) == ["events"]
    events = _events(_call("c1", "read"), _started("c1", "mutating", "read"))
    [action] = plan_repairs(events)
    assert action.status == "unknown"


def test_repair_bodies_pair_a_repaired_event_with_a_result() -> None:
    actions = plan_repairs(_events(_call("c1", "bash")))
    bodies = repair_bodies(actions)
    assert bodies == [
        Repaired(call_id="c1", status="interrupted", reason=NOT_EXECUTED),
        ItemEvent(item=ToolResult(call_id="c1", status="interrupted", content=NOT_EXECUTED)),
    ]


def test_repair_bodies_resolve_every_call_and_layers_validate() -> None:
    events = _events(
        _call("a"), _call("b"), _call("c"), _started("a", "read_only"), _started("b", "external")
    )
    bodies = repair_bodies(plan_repairs(events))
    appended = [make_event(len(events) + i, body) for i, body in enumerate(bodies)]
    restored = [from_json_line(to_json_line(event)) for event in appended]
    assert restored == appended
    assert unresolved_calls([*events, *restored]) == []


def test_unknown_status_flows_into_the_result_item() -> None:
    bodies = repair_bodies(plan_repairs(_events(_call("c1"), _started("c1", "mutating"))))
    item_event = bodies[1]
    assert isinstance(item_event, ItemEvent)
    assert isinstance(item_event.item, ToolResult)
    assert item_event.item.status == "unknown"
    assert item_event.item.content == MUTATING_LOST


def test_planning_is_idempotent_after_repairs_are_logged() -> None:
    events = _events(_call("a"), _call("b"), _started("b", "mutating"))
    bodies = repair_bodies(plan_repairs(events))
    repaired = [*events, *(make_event(len(events) + i, body) for i, body in enumerate(bodies))]
    assert plan_repairs(repaired) == []
    assert repair_bodies([]) == []
