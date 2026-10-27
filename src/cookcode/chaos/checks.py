from collections import Counter
from collections.abc import Sequence

from cookcode.agent.repair import unresolved_calls
from cookcode.core.events import Event, ItemEvent
from cookcode.core.items import ToolCall, ToolResult


def resolution_problems(events: Sequence[Event]) -> list[str]:
    problems: list[str] = []
    expected = list(range(len(events)))
    if [event.seq for event in events] != expected:
        problems.append("seqs are not contiguous from 0")
    unresolved = unresolved_calls(events)
    if unresolved:
        problems.append(f"unresolved calls: {[call.id for call in unresolved]}")
    counts = Counter(
        body.item.call_id
        for event in events
        if isinstance(body := event.body, ItemEvent) and isinstance(body.item, ToolResult)
    )
    for event in events:
        body = event.body
        if isinstance(body, ItemEvent) and isinstance(body.item, ToolCall):
            n = counts[body.item.id]
            if n > 1:
                problems.append(f"call {body.item.id} has {n} results")
    return problems
