from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from harness.core.events import (
    Event,
    EventBody,
    ItemEvent,
    Repaired,
    ToolCallFinished,
    ToolCallStarted,
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


class RepairAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    tool: str
    status: Literal["interrupted", "unknown"]
    reason: str


def unresolved_calls(events: Sequence[Event]) -> list[ToolCall]:
    resolved: set[str] = set()
    for event in events:
        body = event.body
        if isinstance(body, ItemEvent) and isinstance(body.item, ToolResult):
            resolved.add(body.item.call_id)
    return [
        body.item
        for event in events
        if isinstance(body := event.body, ItemEvent)
        and isinstance(body.item, ToolCall)
        and body.item.id not in resolved
    ]


def plan_repairs(events: Sequence[Event]) -> list[RepairAction]:
    started: dict[str, ToolCallStarted] = {}
    finished: dict[str, ToolCallFinished] = {}
    for event in events:
        if isinstance(event.body, ToolCallStarted):
            started[event.body.call_id] = event.body
        elif isinstance(event.body, ToolCallFinished):
            finished[event.body.call_id] = event.body

    actions: list[RepairAction] = []
    for call in unresolved_calls(events):
        record = started.get(call.id)
        if record is None:
            actions.append(
                RepairAction(
                    call_id=call.id, tool=call.name, status="interrupted", reason=NOT_EXECUTED
                )
            )
            continue
        if record.side_effect == "read_only":
            status: Literal["interrupted", "unknown"] = "interrupted"
            reason = READ_ONLY_LOST
        else:
            status = "unknown"
            reason = MUTATING_LOST
        end = finished.get(call.id)
        if end is not None:
            reason += f" It finished with exit status {end.exit_status}, but its result was lost."
        actions.append(RepairAction(call_id=call.id, tool=call.name, status=status, reason=reason))
    return actions


def repair_bodies(actions: Sequence[RepairAction]) -> list[EventBody]:
    bodies: list[EventBody] = []
    for action in actions:
        bodies.append(Repaired(call_id=action.call_id, status=action.status, reason=action.reason))
        bodies.append(
            ItemEvent(
                item=ToolResult(call_id=action.call_id, status=action.status, content=action.reason)
            )
        )
    return bodies
