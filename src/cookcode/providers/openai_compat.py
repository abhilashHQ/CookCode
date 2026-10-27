import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cookcode.core.ids import new_id
from cookcode.core.items import (
    Item,
    Message,
    OtherItem,
    Reasoning,
    ToolCall,
    ToolResult,
    text_message,
)
from cookcode.core.usage import StopReason, Usage
from cookcode.providers.base import (
    ItemDelta,
    ItemDone,
    ItemStarted,
    ModelCaps,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    UsageReport,
)
from cookcode.providers.http import (
    JSON,
    RETRYABLE_STATUS,
    StreamFailure,
    StreamingHttpProvider,
    as_array,
    as_object,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_CAPS = ModelCaps(
    context_window=128_000, parallel_tool_calls=True, reasoning="opaque", prompt_caching=False
)
EMPTY_RESULT = "(no output)"
REASONING_TEXT_FIELDS = ("reasoning", "reasoning_content")
FINISH_REASONS: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}

def _result_content(result: ToolResult) -> str:
    content = result.content or EMPTY_RESULT
    return content if result.status == "ok" else f"[{result.status}] {content}"


def _wire_call(call: ToolCall) -> JSON:
    arguments = call.raw_arguments
    if arguments is None:
        arguments = json.dumps(call.arguments, ensure_ascii=False)
    return {
        "id": call.provider_call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": arguments},
    }


@dataclass
class _Turn:
    text: list[str] = field(default_factory=list[str])
    calls: list[JSON] = field(default_factory=list[JSON])
    details: list[Any] = field(default_factory=list[Any])
    reasoning: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def message(self) -> JSON:
        message: JSON = {"role": "assistant", "content": "".join(self.text) or None}
        if self.calls:
            message["tool_calls"] = self.calls
        elif message["content"] is None:
            message["content"] = ""
        if self.details:
            message["reasoning_details"] = self.details
        else:
            for name, parts in self.reasoning.items():
                message[name] = "".join(parts)
        return message


def to_messages(request: ModelRequest, *, provider: str) -> list[JSON]:
    provider_ids = {
        item.id: item.provider_call_id for item in request.items if isinstance(item, ToolCall)
    }
    messages: list[JSON] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    turn: _Turn | None = None

    def flush() -> None:
        nonlocal turn
        if turn is not None:
            messages.append(turn.message())
            turn = None

    for item in request.items:
        if isinstance(item, Message) and item.role != "assistant":
            flush()
            messages.append({"role": item.role, "content": item.text})
        elif isinstance(item, Message):
            turn = turn or _Turn()
            turn.text.append(item.text)
        elif isinstance(item, ToolCall):
            turn = turn or _Turn()
            turn.calls.append(_wire_call(item))
        elif isinstance(item, Reasoning):
            if not item.replayable_to(provider, request.model):
                continue
            turn = turn or _Turn()
            turn.details.extend(as_array(item.payload.get("reasoning_details")))
            for name in REASONING_TEXT_FIELDS:
                text = item.payload.get(name)
                if isinstance(text, str):
                    turn.reasoning.setdefault(name, []).append(text)
        elif isinstance(item, ToolResult):
            flush()
            if item.call_id not in provider_ids:
                raise ValueError(f"tool result {item.call_id} has no matching call in the request")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": provider_ids[item.call_id],
                    "content": _result_content(item),
                }
            )
        elif isinstance(item, OtherItem) and item.provider == provider:
            flush()
            messages.append(dict(item.raw))
    flush()
    return messages


def build_body(
    request: ModelRequest, *, provider: str, extra: Mapping[str, Any] | None = None
) -> JSON:
    body: JSON = {
        "model": request.model,
        "messages": to_messages(request, provider=provider),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if extra:
        body.update(extra)
    return body


def _usage(raw: JSON) -> Usage:
    def number(value: Any) -> int:
        return value if isinstance(value, int) else 0

    prompt = as_object(raw.get("prompt_tokens_details")) or {}
    completion = as_object(raw.get("completion_tokens_details")) or {}
    cache_read = number(prompt.get("cached_tokens"))
    cache_write = number(prompt.get("cache_write_tokens"))
    return Usage(
        input=max(number(raw.get("prompt_tokens")) - cache_read - cache_write, 0),
        cache_read=cache_read,
        cache_write=cache_write,
        output=number(raw.get("completion_tokens")),
        reasoning=number(completion.get("reasoning_tokens")),
    )


def _merge_detail(details: dict[int, JSON], order: list[int], detail: JSON) -> None:
    index = detail.get("index")
    key = index if isinstance(index, int) else len(order)
    if key not in details:
        details[key] = dict(detail)
        order.append(key)
        return
    current = details[key]
    for name, value in detail.items():
        if name in ("text", "summary", "data") and isinstance(value, str):
            previous = current.get(name)
            current[name] = (previous if isinstance(previous, str) else "") + value
        elif value is not None:
            current[name] = value


@dataclass
class _CallParts:
    id: str | None = None
    name: str = ""
    arguments: list[str] = field(default_factory=list[str])


def _tool_call(parts: _CallParts) -> ToolCall:
    text = "".join(parts.arguments)
    raw: str | None = None
    arguments: JSON = {}
    if text.strip():
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        parsed_object = as_object(parsed)
        if parsed_object is not None:
            arguments = parsed_object
        else:
            raw = text
    call_id = new_id()
    return ToolCall(
        id=call_id,
        provider_call_id=parts.id or f"call_{call_id[:24]}",
        name=parts.name,
        arguments=arguments,
        raw_arguments=raw,
    )


class ChatStreamAssembler:
    """Turns chat-completion chunks into display events, and into items once the response ends."""

    def __init__(self, *, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model
        self._indexes: dict[str, int] = {}
        self._text: list[str] = []
        self._reasoning: dict[str, list[str]] = {}
        self._details: dict[int, JSON] = {}
        self._detail_order: list[int] = []
        self._calls: dict[int, _CallParts] = {}
        self._finish: str | None = None
        self._usage: JSON | None = None

    def _start(self, key: str, kind: str) -> list[StreamEvent]:
        if key in self._indexes:
            return []
        self._indexes[key] = len(self._indexes)
        return [ItemStarted(index=self._indexes[key], kind=kind)]

    def feed(self, chunk: JSON) -> list[StreamEvent]:
        error = as_object(chunk.get("error"))
        if error is not None:
            raise _chunk_failure(error)
        usage = as_object(chunk.get("usage"))
        if usage is not None:
            self._usage = usage
        events: list[StreamEvent] = []
        for value in as_array(chunk.get("choices")):
            choice = as_object(value)
            if choice is None or choice.get("index", 0) != 0:
                continue
            events.extend(self._feed_delta(as_object(choice.get("delta")) or {}))
            finish = choice.get("finish_reason")
            if isinstance(finish, str):
                self._finish = finish
        return events

    def _feed_delta(self, delta: JSON) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        shown = ""
        for name in REASONING_TEXT_FIELDS:
            text = delta.get(name)
            if isinstance(text, str) and text:
                self._reasoning.setdefault(name, []).append(text)
                shown = shown or text
        for value in as_array(delta.get("reasoning_details")):
            detail = as_object(value)
            if detail is None:
                continue
            _merge_detail(self._details, self._detail_order, detail)
            text = detail.get("text")
            if not shown and isinstance(text, str):
                shown = text
        if shown or self._details or self._reasoning:
            events.extend(self._start("reasoning", "reasoning"))
        if shown:
            events.append(ItemDelta(index=self._indexes["reasoning"], text=shown))
        content = delta.get("content")
        if isinstance(content, str) and content:
            events.extend(self._start("message", "message"))
            self._text.append(content)
            events.append(ItemDelta(index=self._indexes["message"], text=content))
        for value in as_array(delta.get("tool_calls")):
            wire = as_object(value)
            if wire is None:
                continue
            position = wire.get("index")
            slot = position if isinstance(position, int) else len(self._calls)
            events.extend(self._start(f"call:{slot}", "tool_call"))
            parts = self._calls.setdefault(slot, _CallParts())
            call_id = wire.get("id")
            if isinstance(call_id, str) and call_id:
                parts.id = call_id
            function = as_object(wire.get("function")) or {}
            name = function.get("name")
            if isinstance(name, str):
                parts.name += name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                parts.arguments.append(arguments)
        return events

    @property
    def finished(self) -> bool:
        return self._finish is not None

    def finish(self) -> list[StreamEvent]:
        if self._finish is None:
            raise StreamFailure("stream ended before a finish_reason", retryable=True)
        if self._finish == "error":
            raise StreamFailure(
                "provider ended the response with finish_reason=error", retryable=True
            )
        keyed: list[tuple[int, Item]] = []
        if "reasoning" in self._indexes:
            payload: JSON = {}
            if self._details:
                payload["reasoning_details"] = [self._details[key] for key in self._detail_order]
            texts = {name: "".join(parts) for name, parts in self._reasoning.items()}
            payload.update(texts)
            summary = next(iter(texts.values()), "") or "".join(
                str(self._details[key].get("text") or self._details[key].get("summary") or "")
                for key in self._detail_order
            )
            keyed.append(
                (
                    self._indexes["reasoning"],
                    Reasoning(
                        provider=self._provider,
                        model_family=self._model,
                        payload=payload,
                        summary_text=summary or None,
                    ),
                )
            )
        if "message" in self._indexes:
            keyed.append((self._indexes["message"], text_message("assistant", "".join(self._text))))
        for slot, parts in self._calls.items():
            keyed.append((self._indexes[f"call:{slot}"], _tool_call(parts)))
        keyed.sort(key=lambda pair: pair[0])
        events: list[StreamEvent] = [ItemDone(index=index, item=item) for index, item in keyed]
        raw = self._usage or {}
        events.append(UsageReport(usage=_usage(raw), raw=raw))
        has_calls = any(isinstance(item, ToolCall) for _, item in keyed)
        stop = FINISH_REASONS.get(self._finish, "end_turn")
        events.append(StreamEnd(stop_reason="tool_use" if has_calls else stop))
        return events


def _chunk_failure(error: JSON) -> StreamFailure:
    code = error.get("code")
    status = code if isinstance(code, int) else None
    message = str(error.get("message") or error)
    metadata = as_object(error.get("metadata")) or {}
    if metadata.get("raw"):
        message = f"{message}: {str(metadata['raw'])[:300]}"
    retryable = status is None or status in RETRYABLE_STATUS
    return StreamFailure(f"stream error {status}: {message}", retryable=retryable, status=status)


class OpenAICompatProvider(StreamingHttpProvider):
    """Chat Completions over raw HTTP. Covers OpenRouter and any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = OPENROUTER_BASE_URL,
        name: str = "openrouter",
        caps: ModelCaps = DEFAULT_CAPS,
        extra_body: Mapping[str, Any] | None = None,
        **transport_policy: Any,
    ) -> None:
        super().__init__(name=name, caps=caps, **transport_policy)
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._extra_body = dict(extra_body or {})

    def build_body(self, request: ModelRequest) -> JSON:
        return build_body(request, provider=self.name, extra=self._extra_body)

    def url(self, model: str) -> str:
        return self._url

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def assembler(self, model: str) -> ChatStreamAssembler:
        return ChatStreamAssembler(provider=self.name, model=model)

    def error_failure(self, error: JSON) -> StreamFailure:
        return _chunk_failure(error)

    def is_terminator(self, data: str) -> bool:
        return data.strip() == "[DONE]"
