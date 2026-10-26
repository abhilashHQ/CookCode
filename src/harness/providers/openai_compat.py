import asyncio
import json
import random
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from harness.core.cancel import CancelToken
from harness.core.ids import new_id
from harness.core.items import (
    Item,
    Message,
    OtherItem,
    Reasoning,
    ToolCall,
    ToolResult,
    text_message,
)
from harness.core.usage import StopReason, Usage
from harness.providers.base import (
    ItemDelta,
    ItemDone,
    ItemStarted,
    ModelCaps,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    StreamRetry,
    UsageReport,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_CAPS = ModelCaps(
    context_window=128_000, parallel_tool_calls=True, reasoning="opaque", prompt_caching=False
)
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, *range(520, 530)})
EMPTY_RESULT = "(no output)"
REASONING_TEXT_FIELDS = ("reasoning", "reasoning_content")
FINISH_REASONS: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}

type JSON = dict[str, Any]


def _object(value: object) -> JSON | None:
    return cast(JSON, value) if isinstance(value, dict) else None


def _array(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


class StreamFailure(Exception):
    def __init__(
        self,
        reason: str,
        *,
        retryable: bool,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.status = status
        self.retry_after_s = retry_after_s


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
            turn.details.extend(_array(item.payload.get("reasoning_details")))
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


class SSEDecoder:
    def __init__(self) -> None:
        self._data: list[str] = []

    def feed(self, line: str) -> str | None:
        if line == "":
            return self.flush()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        if name == "data":
            self._data.append(value.removeprefix(" "))
        return None

    def flush(self) -> str | None:
        if not self._data:
            return None
        data = "\n".join(self._data)
        self._data = []
        return data


def _usage(raw: JSON) -> Usage:
    def number(value: Any) -> int:
        return value if isinstance(value, int) else 0

    prompt = _object(raw.get("prompt_tokens_details")) or {}
    completion = _object(raw.get("completion_tokens_details")) or {}
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
        parsed_object = _object(parsed)
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
        error = _object(chunk.get("error"))
        if error is not None:
            raise _chunk_failure(error)
        usage = _object(chunk.get("usage"))
        if usage is not None:
            self._usage = usage
        events: list[StreamEvent] = []
        for value in _array(chunk.get("choices")):
            choice = _object(value)
            if choice is None or choice.get("index", 0) != 0:
                continue
            events.extend(self._feed_delta(_object(choice.get("delta")) or {}))
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
        for value in _array(delta.get("reasoning_details")):
            detail = _object(value)
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
        for value in _array(delta.get("tool_calls")):
            wire = _object(value)
            if wire is None:
                continue
            position = wire.get("index")
            slot = position if isinstance(position, int) else len(self._calls)
            events.extend(self._start(f"call:{slot}", "tool_call"))
            parts = self._calls.setdefault(slot, _CallParts())
            call_id = wire.get("id")
            if isinstance(call_id, str) and call_id:
                parts.id = call_id
            function = _object(wire.get("function")) or {}
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
    metadata = _object(error.get("metadata")) or {}
    if metadata.get("raw"):
        message = f"{message}: {str(metadata['raw'])[:300]}"
    retryable = status is None or status in RETRYABLE_STATUS
    return StreamFailure(f"stream error {status}: {message}", retryable=retryable, status=status)


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def server_wait(headers: httpx.Headers, now: float) -> float | None:
    """Seconds the server asks us to wait: Retry-After, or the reset of an exhausted limit."""
    retry_after = _number(headers.get("retry-after"))
    if retry_after is not None:
        return max(retry_after, 0.0)
    reset_ms = _number(headers.get("x-ratelimit-reset"))
    if _number(headers.get("x-ratelimit-remaining")) == 0 and reset_ms is not None:
        return max(reset_ms / 1000 - now, 0.0)
    return None


async def _http_failure(response: httpx.Response, now: float) -> StreamFailure:
    body = (await response.aread()).decode("utf-8", "replace")
    try:
        error = _object((_object(json.loads(body)) or {}).get("error"))
        if error is not None:
            body = _chunk_failure(error).reason
    except json.JSONDecodeError:
        pass
    status = response.status_code
    return StreamFailure(
        f"HTTP {status}: {body[:500]}",
        retryable=status in RETRYABLE_STATUS,
        status=status,
        retry_after_s=server_wait(response.headers, now),
    )


class OpenAICompatProvider:
    """Chat Completions over raw HTTP. Covers OpenRouter and any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = OPENROUTER_BASE_URL,
        name: str = "openrouter",
        caps: ModelCaps = DEFAULT_CAPS,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 4,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 30.0,
        connect_timeout_s: float = 30.0,
        read_timeout_s: float = 180.0,
        extra_body: Mapping[str, Any] | None = None,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._caps = caps
        self._transport = transport
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._timeout = httpx.Timeout(read_timeout_s, connect=connect_timeout_s)
        self._extra_body = dict(extra_body or {})
        self._jitter = jitter
        self._clock = clock

    def capabilities(self, model: str) -> ModelCaps:
        return self._caps

    def build_body(self, request: ModelRequest) -> JSON:
        return build_body(request, provider=self.name, extra=self._extra_body)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _delay(self, attempt: int, failure: StreamFailure) -> float:
        backoff = min(self._backoff_cap_s, self._backoff_base_s * 2 ** (attempt - 1))
        backoff *= 0.5 + self._jitter() / 2
        if failure.retry_after_s is not None:
            backoff = max(backoff, failure.retry_after_s)
        return backoff

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncGenerator[StreamEvent]:
        body = self.build_body(request)
        attempt = 0
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            while True:
                attempt += 1
                if cancel.cancelled:
                    yield StreamEnd(stop_reason="aborted")
                    return
                failure: StreamFailure | None = None
                async with aclosing(self._attempt(client, body, request.model, cancel)) as events:
                    async for event in events:
                        if isinstance(event, StreamFailure):
                            failure = event
                            break
                        yield event
                        if isinstance(event, StreamEnd):
                            return
                assert failure is not None
                wait = failure.retry_after_s
                if failure.retryable and wait is not None and wait > self._backoff_cap_s:
                    yield StreamEnd(
                        stop_reason="error",
                        error=f"{failure.reason} (the server asks to wait {wait:.0f}s, "
                        f"over the {self._backoff_cap_s:.0f}s retry cap)",
                    )
                    return
                if not failure.retryable or attempt > self._max_retries:
                    tries = f" after {attempt} attempts" if attempt > 1 else ""
                    yield StreamEnd(stop_reason="error", error=f"{failure.reason}{tries}")
                    return
                delay = self._delay(attempt, failure)
                yield StreamRetry(
                    attempt=attempt, delay_s=delay, reason=failure.reason, status=failure.status
                )
                try:
                    await asyncio.wait_for(cancel.wait(), timeout=delay)
                except TimeoutError:
                    continue
                yield StreamEnd(stop_reason="aborted")
                return

    async def _attempt(
        self, client: httpx.AsyncClient, body: JSON, model: str, cancel: CancelToken
    ) -> AsyncGenerator[StreamEvent | StreamFailure]:
        queue: asyncio.Queue[StreamEvent | StreamFailure | None] = asyncio.Queue()
        pump = asyncio.create_task(self._pump(client, body, model, queue))
        stopping = asyncio.ensure_future(cancel.wait())
        try:
            while True:
                getting = asyncio.ensure_future(queue.get())
                await asyncio.wait({getting, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if not getting.done():
                    getting.cancel()
                    yield StreamEnd(stop_reason="aborted")
                    return
                event = getting.result()
                if event is None:
                    return
                yield event
                if isinstance(event, StreamFailure | StreamEnd):
                    return
        finally:
            stopping.cancel()
            pump.cancel()
            await asyncio.gather(pump, stopping, return_exceptions=True)

    async def _pump(
        self,
        client: httpx.AsyncClient,
        body: JSON,
        model: str,
        queue: asyncio.Queue[StreamEvent | StreamFailure | None],
    ) -> None:
        try:
            async with client.stream(
                "POST", self._url, json=body, headers=self._headers()
            ) as response:
                if response.status_code != 200:
                    await queue.put(await _http_failure(response, self._clock()))
                    return
                assembler = ChatStreamAssembler(provider=self.name, model=model)
                decoder = SSEDecoder()
                async for line in response.aiter_lines():
                    data = decoder.feed(line)
                    if data is None:
                        continue
                    if data.strip() == "[DONE]":
                        break
                    for event in assembler.feed(_parse_chunk(data)):
                        await queue.put(event)
                else:
                    data = decoder.flush()
                    if data is not None and data.strip() != "[DONE]":
                        for event in assembler.feed(_parse_chunk(data)):
                            await queue.put(event)
                for event in assembler.finish():
                    await queue.put(event)
        except StreamFailure as failure:
            await queue.put(failure)
        except httpx.TransportError as exc:
            await queue.put(
                StreamFailure(f"{type(exc).__name__}: {exc or 'transport error'}", retryable=True)
            )
        except Exception as exc:
            await queue.put(StreamFailure(f"{type(exc).__name__}: {exc}", retryable=False))
        finally:
            await queue.put(None)


def _parse_chunk(data: str) -> JSON:
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError as exc:
        raise StreamFailure(f"malformed stream chunk: {data[:200]!r}", retryable=True) from exc
    parsed = _object(chunk)
    if parsed is None:
        raise StreamFailure(f"stream chunk is not an object: {data[:200]!r}", retryable=True)
    return parsed
