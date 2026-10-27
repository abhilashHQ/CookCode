import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import TypeAdapter

from cookcode.core.events import (
    CheckpointRestored,
    CompactionApplied,
    Event,
    HookBlocked,
    ItemEvent,
    ModelRequestStarted,
    SteerMessage,
    UserMessage,
)
from cookcode.core.items import Item, Message, Reasoning, ToolCall, ToolResult, text_message
from cookcode.core.strategy import Strategy
from cookcode.providers.base import ModelRequest, ToolSchema

MASK_MIN_CHARS = 200
SUMMARY_HEADER = "[summary of the earlier part of this session]"
SUMMARIZE_VIEW = "summarize"
MEMORY_VIEW = "memory"
SIDE_VIEWS = frozenset({SUMMARIZE_VIEW, MEMORY_VIEW})


@dataclass(frozen=True)
class _Entry:
    seq: int
    step: int
    item: Item


def _is_step(event: Event) -> bool:
    body = event.body
    return isinstance(body, ModelRequestStarted) and body.view not in SIDE_VIEWS


def _entries(events: Sequence[Event]) -> list[_Entry]:
    """Projected items with their log position and the step (agent model request index) they
    belong to. Items before the first request have step -1."""
    entries: list[_Entry] = []
    step = -1
    for event in events:
        body = event.body
        if _is_step(event):
            step += 1
        elif isinstance(body, UserMessage | SteerMessage):
            entries.append(_Entry(event.seq, step, text_message("user", body.text)))
        elif isinstance(body, ItemEvent):
            entries.append(_Entry(event.seq, step, body.item))
        elif isinstance(body, CheckpointRestored):
            turns = sorted(set(body.turns))
            note = text_message(
                "user",
                f"[undo] Restored {', '.join(body.paths)} to their state before turn "
                f"{min(body.turns, default=0)}. File edits made in turns "
                f"{', '.join(str(turn) for turn in turns)} are gone.",
            )
            entries.append(_Entry(event.seq, step, note))
        elif isinstance(body, HookBlocked) and body.point == "stop":
            note = text_message("user", f"[{body.hook}] {body.message}")
            entries.append(_Entry(event.seq, step, note))
    return entries


def _project(events: Sequence[Event]) -> list[Item]:
    return [entry.item for entry in _entries(events)]


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


def requests_made(events: Sequence[Event]) -> int:
    return sum(_is_step(event) for event in events)


def latest_summary(events: Sequence[Event]) -> CompactionApplied | None:
    for event in reversed(events):
        if isinstance(event.body, CompactionApplied) and event.body.summary is not None:
            return event.body
    return None


def _apply_summary(entries: list[_Entry], summary: CompactionApplied) -> list[_Entry]:
    kept = [e for e in entries if not summary.from_seq <= e.seq <= summary.to_seq]
    at = next((i for i, e in enumerate(kept) if e.seq > summary.to_seq), len(kept))
    note = text_message("user", f"{SUMMARY_HEADER}\n{summary.summary}")
    return [*kept[:at], _Entry(summary.from_seq, -1, note), *kept[at:]]


def mask_cutoff(strategy: Strategy, made: int) -> int:
    """Results of steps below the cutoff are masked. Batched masking moves the cutoff in jumps
    of batch_steps, so requests between jumps keep the same prefix and stay cacheable."""
    cutoff = made - strategy.keep_steps
    if strategy.batched:
        cutoff = (cutoff // strategy.batch_steps) * strategy.batch_steps
    return cutoff


def placeholder(call: ToolCall, chars: int) -> str:
    return (
        f"[masked to save context: {chars} chars of {call.name} output. "
        f'recall(call_id="{call.provider_call_id}") returns it.]'
    )


def _mask(entries: list[_Entry], strategy: Strategy, made: int) -> list[_Entry]:
    cutoff = mask_cutoff(strategy, made)
    calls = {e.item.id: e.item for e in entries if isinstance(e.item, ToolCall)}
    masked: list[_Entry] = []
    for entry in entries:
        item = entry.item
        if (
            isinstance(item, ToolResult)
            and entry.step < cutoff
            and len(item.content) > MASK_MIN_CHARS
            and item.call_id in calls
        ):
            text = placeholder(calls[item.call_id], len(item.content))
            entry = _Entry(entry.seq, entry.step, item.model_copy(update={"content": text}))
        masked.append(entry)
    return masked


def build_request(
    events: Sequence[Event],
    *,
    model: str,
    system: str,
    tools: tuple[ToolSchema, ...],
    max_output_tokens: int | None = None,
    provider: str | None = None,
    strategy: Strategy | None = None,
) -> ModelRequest:
    """Reasoning from another provider or model is dropped: its payloads are opaque and only
    the model that produced them can read them. Then the strategy applies: the latest logged
    summary replaces the range it covers, and tool outputs older than the mask cutoff become
    placeholders that name the call for `recall`."""
    strategy = strategy or Strategy()
    entries = [e for e in _entries(events) if not _unreplayable(e.item, provider, model)]
    if strategy.summarizes:
        summary = latest_summary(events)
        if summary is not None:
            entries = _apply_summary(entries, summary)
    if strategy.masks:
        entries = _mask(entries, strategy, requests_made(events))
    return ModelRequest(
        model=model,
        system=system,
        tools=tools,
        items=tuple(_reorder_results([entry.item for entry in entries])),
        max_output_tokens=max_output_tokens,
    )


_ITEMS: TypeAdapter[tuple[Item, ...]] = TypeAdapter(tuple[Item, ...])
_ITEM: TypeAdapter[Item] = TypeAdapter(Item)


def items_sha256(items: tuple[Item, ...]) -> str:
    """Fingerprint of the history part of a request. System prompt and tools are pinned by the
    run config, so replay can verify the projection from the log alone."""
    return hashlib.sha256(_ITEMS.dump_json(items)).hexdigest()


def estimate_tokens(request: ModelRequest) -> int:
    return len(request.model_dump_json()) // 4


def token_ledger(request: ModelRequest) -> dict[str, int]:
    """Estimated tokens (chars / 4) per category. `stats` calibrates them against the input
    tokens the provider reports."""
    chars = dict.fromkeys(("system", "tools", "user", "assistant", "reasoning", "tool_output"), 0)
    chars["system"] = len(request.system)
    chars["tools"] = sum(len(tool.model_dump_json()) for tool in request.tools)
    for item in request.items:
        if isinstance(item, Message):
            chars["user" if item.role == "user" else "assistant"] += len(item.text)
        elif isinstance(item, ToolCall):
            chars["assistant"] += len(item.name) + len(json.dumps(item.arguments))
        elif isinstance(item, Reasoning):
            chars["reasoning"] += len(json.dumps(item.payload)) + len(item.summary_text or "")
        elif isinstance(item, ToolResult):
            chars["tool_output"] += len(item.content)
    return {category: count // 4 for category, count in chars.items()}


def shared_prefix_tokens(previous: Sequence[Item], current: Sequence[Item]) -> int:
    """Estimated tokens of history the current request shares with the previous one, item for
    item from the start: what a prefix cache can serve beyond the system prompt and tools."""
    shared = 0
    for before, after in zip(previous, current, strict=False):
        if before != after:
            break
        shared += len(_ITEM.dump_json(after))
    return shared // 4


def summary_cut(events: Sequence[Event], keep_steps: int) -> tuple[int, int] | None:
    """The log range a new summary covers: from just after the first user message to just
    before the request that starts the last `keep_steps` steps. Summaries are cumulative, so a
    new one starts at the same place and has to reach further than the last."""
    first_user = next((e.seq for e in events if isinstance(e.body, UserMessage)), None)
    starts = [e.seq for e in events if _is_step(e)]
    if first_user is None or len(starts) <= keep_steps:
        return None
    from_seq, to_seq = first_user + 1, starts[-keep_steps] - 1
    previous = latest_summary(events)
    if to_seq < from_seq or (previous is not None and previous.to_seq >= to_seq):
        return None
    return from_seq, to_seq


def transcript(events: Sequence[Event], from_seq: int, to_seq: int) -> str:
    """The summarizer's input as plain text, so it needs no tools and no reasoning replay.
    A previous summary stands in for the range it already covers."""
    lines: list[str] = []
    previous = latest_summary(events)
    if previous is not None:
        lines.append(f"Earlier summary:\n{previous.summary}")
        from_seq = previous.to_seq + 1
    for entry in _entries(events):
        if not from_seq <= entry.seq <= to_seq:
            continue
        item = entry.item
        if isinstance(item, Message):
            lines.append(f"[{item.role}] {item.text}")
        elif isinstance(item, ToolCall):
            lines.append(f"[call {item.name}] {json.dumps(item.arguments, ensure_ascii=False)}")
        elif isinstance(item, ToolResult):
            lines.append(f"[result {item.status}] {item.content}")
    return "\n".join(lines)
