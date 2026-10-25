from collections.abc import Sequence

from harness.core.events import Event, ItemEvent, SteerMessage, UserMessage
from harness.core.items import Item, Message, ToolCall, ToolResult, text_message
from harness.providers.base import ModelRequest, ToolSchema


def _project(events: Sequence[Event]) -> list[Item]:
    items: list[Item] = []
    for event in events:
        body = event.body
        if isinstance(body, UserMessage | SteerMessage):
            items.append(text_message("user", body.text))
        elif isinstance(body, ItemEvent):
            items.append(body.item)
    return items


def _reorder_results(items: list[Item]) -> list[Item]:
    ordered: list[Item] = []
    call_block: list[str] = []
    new_response = True
    index = 0
    while index < len(items):
        item = items[index]
        if isinstance(item, ToolCall):
            if new_response:
                call_block = []
                new_response = False
            call_block.append(item.id)
            ordered.append(item)
            index += 1
        elif isinstance(item, ToolResult):
            end = index
            while end < len(items) and isinstance(items[end], ToolResult):
                end += 1
            run = [r for r in items[index:end] if isinstance(r, ToolResult)]
            position = {call_id: i for i, call_id in reversed(list(enumerate(call_block)))}
            run.sort(key=lambda r: position.get(r.call_id, len(call_block)))
            ordered.extend(run)
            new_response = True
            index = end
        else:
            if isinstance(item, Message) and item.role == "user":
                new_response = True
            ordered.append(item)
            index += 1
    return ordered


def build_request(
    events: Sequence[Event],
    *,
    model: str,
    system: str,
    tools: tuple[ToolSchema, ...],
    max_output_tokens: int | None = None,
) -> ModelRequest:
    return ModelRequest(
        model=model,
        system=system,
        tools=tools,
        items=tuple(_reorder_results(_project(events))),
        max_output_tokens=max_output_tokens,
    )


def estimate_tokens(request: ModelRequest) -> int:
    return len(request.model_dump_json()) // 4
