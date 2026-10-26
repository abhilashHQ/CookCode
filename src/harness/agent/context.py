import hashlib
from collections.abc import Sequence

from pydantic import TypeAdapter

from harness.core.events import CheckpointRestored, Event, ItemEvent, SteerMessage, UserMessage
from harness.core.items import Item, Message, Reasoning, ToolCall, ToolResult, text_message
from harness.providers.base import ModelRequest, ToolSchema


def _project(events: Sequence[Event]) -> list[Item]:
    items: list[Item] = []
    for event in events:
        body = event.body
        if isinstance(body, UserMessage | SteerMessage):
            items.append(text_message("user", body.text))
        elif isinstance(body, ItemEvent):
            items.append(body.item)
        elif isinstance(body, CheckpointRestored):
            turns = sorted(set(body.turns))
            items.append(
                text_message(
                    "user",
                    f"[undo] Restored {', '.join(body.paths)} to their state before turn "
                    f"{min(body.turns, default=0)}. File edits made in turns "
                    f"{', '.join(str(turn) for turn in turns)} are gone.",
                )
            )
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


def _unreplayable(item: Item, provider: str | None, model: str) -> bool:
    return (
        provider is not None
        and isinstance(item, Reasoning)
        and not item.replayable_to(provider, model)
    )


def dropped_reasoning(events: Sequence[Event], *, provider: str, model: str) -> int:
    return sum(_unreplayable(item, provider, model) for item in _project(events))


def build_request(
    events: Sequence[Event],
    *,
    model: str,
    system: str,
    tools: tuple[ToolSchema, ...],
    max_output_tokens: int | None = None,
    provider: str | None = None,
) -> ModelRequest:
    """Reasoning from another provider or model is dropped: its payloads are opaque and only
    the model that produced them can read them."""
    items = [item for item in _project(events) if not _unreplayable(item, provider, model)]
    return ModelRequest(
        model=model,
        system=system,
        tools=tools,
        items=tuple(_reorder_results(items)),
        max_output_tokens=max_output_tokens,
    )


_ITEMS: TypeAdapter[tuple[Item, ...]] = TypeAdapter(tuple[Item, ...])


def items_sha256(items: tuple[Item, ...]) -> str:
    """Fingerprint of the history part of a request. System prompt and tools are pinned by the
    run config, so replay can verify the projection from the log alone."""
    return hashlib.sha256(_ITEMS.dump_json(items)).hexdigest()


def estimate_tokens(request: ModelRequest) -> int:
    return len(request.model_dump_json()) // 4
