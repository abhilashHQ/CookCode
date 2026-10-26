"""Adapter B: Gemini generateContent over raw HTTP (streamGenerateContent, SSE)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from harness.core.ids import new_id
from harness.core.items import Message, OtherItem, Reasoning, ToolCall, ToolResult, text_message
from harness.core.usage import StopReason, Usage
from harness.providers.base import (
    ItemDelta,
    ItemDone,
    ItemStarted,
    ModelCaps,
    ModelRequest,
    StreamEnd,
    StreamEvent,
    UsageReport,
)
from harness.providers.http import (
    JSON,
    RETRYABLE_STATUS,
    StreamFailure,
    StreamingHttpProvider,
    as_array,
    as_object,
)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_CAPS = ModelCaps(
    context_window=1_000_000, parallel_tool_calls=True, reasoning="opaque", prompt_caching=True
)
EMPTY_RESULT = "(no output)"
SYNTHETIC_ID = "harness-call-"
# Documented value that tells Gemini 3+ to skip signature validation for calls it did not sign.
FOREIGN_SIGNATURE = "skip_thought_signature_validator"
TEXT_SIGNATURE = "text"
FINISH_REASONS: dict[str, StopReason] = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refusal",
    "RECITATION": "refusal",
    "LANGUAGE": "refusal",
    "BLOCKLIST": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "SPII": "refusal",
}
RESAMPLE = frozenset({"MALFORMED_FUNCTION_CALL", "MALFORMED_RESPONSE"})


def _result_payload(result: ToolResult) -> JSON:
    content = result.content or EMPTY_RESULT
    if result.status == "ok":
        return {"output": content}
    return {"error": f"[{result.status}] {content}"}


@dataclass
class _Content:
    role: str
    parts: list[JSON] = field(default_factory=list[JSON])


def _signatures(item: Reasoning) -> dict[str, str]:
    raw = as_object(item.payload.get("signatures")) or {}
    return {key: value for key, value in raw.items() if isinstance(value, str)}


def to_contents(request: ModelRequest, *, provider: str) -> list[JSON]:
    """Items to Gemini contents. Signatures go back on the part they came with; calls in the
    current turn that Gemini never signed get the documented skip value."""
    calls = {item.id: item for item in request.items if isinstance(item, ToolCall)}
    contents: list[_Content] = []
    signatures: dict[str, str] = {}

    def flush_text_signature() -> None:
        signature = signatures.pop(TEXT_SIGNATURE, None)
        if signature is not None:
            if not contents or contents[-1].role != "model":
                contents.append(_Content("model"))
            contents[-1].parts.append({"text": "", "thoughtSignature": signature})

    def content(role: str) -> _Content:
        if role != "model":
            flush_text_signature()
        if not contents or contents[-1].role != role:
            contents.append(_Content(role))
        return contents[-1]

    for item in request.items:
        if isinstance(item, Message):
            role = "model" if item.role == "assistant" else "user"
            part: JSON = {"text": item.text}
            if role == "model" and TEXT_SIGNATURE in signatures:
                part["thoughtSignature"] = signatures.pop(TEXT_SIGNATURE)
            content(role).parts.append(part)
        elif isinstance(item, Reasoning):
            flush_text_signature()
            if item.replayable_to(provider, request.model):
                signatures.update(_signatures(item))
        elif isinstance(item, ToolCall):
            call: JSON = {"name": item.name, "args": item.arguments}
            if not item.provider_call_id.startswith(SYNTHETIC_ID):
                call["id"] = item.provider_call_id
            part = {"functionCall": call}
            if item.provider_call_id in signatures:
                part["thoughtSignature"] = signatures.pop(item.provider_call_id)
            content("model").parts.append(part)
        elif isinstance(item, ToolResult):
            source = calls.get(item.call_id)
            if source is None:
                raise ValueError(f"tool result {item.call_id} has no matching call in the request")
            response: JSON = {"name": source.name, "response": _result_payload(item)}
            if not source.provider_call_id.startswith(SYNTHETIC_ID):
                response["id"] = source.provider_call_id
            content("user").parts.append({"functionResponse": response})
        elif isinstance(item, OtherItem) and item.provider == provider:
            content("model").parts.append(item.raw)
    flush_text_signature()
    _sign_current_turn(contents)
    return [{"role": c.role, "parts": c.parts} for c in contents]


def _is_user_text(content: _Content) -> bool:
    """Gemini starts the current turn at a user message; a content carrying function
    responses is not one, even when a prompt was merged into it."""
    return (
        content.role == "user"
        and any("text" in part for part in content.parts)
        and not any("functionResponse" in part for part in content.parts)
    )


def _sign_current_turn(contents: list[_Content]) -> None:
    start = max((i for i, c in enumerate(contents) if _is_user_text(c)), default=-1)
    for content in contents[start + 1 :]:
        if content.role != "model":
            continue
        first = next((p for p in content.parts if "functionCall" in p), None)
        if first is not None and "thoughtSignature" not in first:
            first["thoughtSignature"] = FOREIGN_SIGNATURE


def build_body(
    request: ModelRequest, *, provider: str, generation: Mapping[str, Any] | None = None
) -> JSON:
    body: JSON = {"contents": to_contents(request, provider=provider)}
    if request.system:
        body["systemInstruction"] = {"parts": [{"text": request.system}]}
    if request.tools:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parametersJsonSchema": tool.parameters,
                    }
                    for tool in request.tools
                ]
            }
        ]
    config: JSON = {"thinkingConfig": {"includeThoughts": True}, **dict(generation or {})}
    if request.max_output_tokens is not None:
        config["maxOutputTokens"] = request.max_output_tokens
    body["generationConfig"] = config
    return body


def _usage(raw: JSON) -> Usage:
    def number(name: str) -> int:
        value = raw.get(name)
        return value if isinstance(value, int) else 0

    cached = number("cachedContentTokenCount")
    thoughts = number("thoughtsTokenCount")
    return Usage(
        input=number("promptTokenCount") - cached + number("toolUsePromptTokenCount"),
        cache_read=cached,
        output=number("candidatesTokenCount") + thoughts,
        reasoning=thoughts,
    )


class GeminiStreamAssembler:
    """Gemini sends whole parts per chunk: text and thoughts as fragments, calls complete."""

    def __init__(self, *, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model
        self._thoughts: list[str] = []
        self._text: list[str] = []
        self._calls: list[ToolCall] = []
        self._other: list[JSON] = []
        self._signatures: dict[str, str] = {}
        self._indexes: dict[str, int] = {}
        self._finish: str | None = None
        self._usage: JSON = {}
        self._blocked: str | None = None

    def _index(self, key: str, kind: str, events: list[StreamEvent]) -> int:
        if key not in self._indexes:
            self._indexes[key] = len(self._indexes)
            events.append(ItemStarted(index=self._indexes[key], kind=kind))
        return self._indexes[key]

    def _slot(self, key: str) -> int:
        return self._indexes.setdefault(key, len(self._indexes))

    def feed(self, chunk: JSON) -> list[StreamEvent]:
        error = as_object(chunk.get("error"))
        if error is not None:
            raise gemini_failure(error)
        usage = as_object(chunk.get("usageMetadata"))
        if usage:
            self._usage = usage
        feedback = as_object(chunk.get("promptFeedback")) or {}
        if isinstance(feedback.get("blockReason"), str):
            self._blocked = str(feedback["blockReason"])
        events: list[StreamEvent] = []
        for candidate in as_array(chunk.get("candidates"))[:1]:
            candidate = as_object(candidate) or {}
            parts = as_array((as_object(candidate.get("content")) or {}).get("parts"))
            for raw in parts:
                part = as_object(raw)
                if part is not None:
                    events.extend(self._part(part))
            if isinstance(candidate.get("finishReason"), str):
                self._finish = str(candidate["finishReason"])
        return events

    def _part(self, part: JSON) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        signature = part.get("thoughtSignature")
        text = part.get("text")
        call = as_object(part.get("functionCall"))
        if call is not None:
            name = call.get("name")
            provided = call.get("id")
            ours = new_id()
            tool_call = ToolCall(
                id=ours,
                provider_call_id=provided
                if isinstance(provided, str) and provided
                else f"{SYNTHETIC_ID}{ours[:12]}",
                name=name if isinstance(name, str) else "",
                arguments=as_object(call.get("args")) or {},
            )
            self._calls.append(tool_call)
            if isinstance(signature, str):
                self._signatures[tool_call.provider_call_id] = signature
            self._index(f"call:{tool_call.id}", "tool_call", events)
        elif isinstance(text, str):
            if part.get("thought") is True:
                index = self._index("reasoning", "reasoning", events)
                self._thoughts.append(text)
            else:
                index = self._index("text", "message", events)
                self._text.append(text)
            if isinstance(signature, str):
                self._signatures[TEXT_SIGNATURE] = signature
            if text:
                events.append(ItemDelta(index=index, text=text))
        else:
            self._other.append(part)
            if isinstance(signature, str):
                self._signatures[TEXT_SIGNATURE] = signature
        return events

    def finish(self) -> list[StreamEvent]:
        if self._blocked is not None and self._finish is None:
            blocked = StreamEnd(stop_reason="refusal", error=f"prompt blocked: {self._blocked}")
            return [*self._usage_events(), blocked]
        if self._finish is None:
            raise StreamFailure("stream ended without a finishReason", retryable=True)
        if self._finish in RESAMPLE:
            raise StreamFailure(f"model returned {self._finish}", retryable=True)
        events: list[StreamEvent] = []
        if self._thoughts or self._signatures:
            payload: JSON = {}
            if self._signatures:
                payload["signatures"] = dict(self._signatures)
            thought = "".join(self._thoughts)
            reasoning = Reasoning(
                provider=self._provider,
                model_family=self._model,
                payload=payload,
                summary_text=thought or None,
            )
            events.append(ItemDone(index=self._slot("reasoning"), item=reasoning))
        if self._text:
            events.append(
                ItemDone(
                    index=self._indexes["text"],
                    item=text_message("assistant", "".join(self._text)),
                )
            )
        for number, part in enumerate(self._other):
            other = OtherItem(provider=self._provider, raw=part)
            events.append(ItemDone(index=self._slot(f"other:{number}"), item=other))
        for call in self._calls:
            events.append(ItemDone(index=self._indexes[f"call:{call.id}"], item=call))
        stop = FINISH_REASONS.get(self._finish)
        if stop == "end_turn" and self._calls:
            stop = "tool_use"
        if stop is None:
            failed = StreamEnd(stop_reason="error", error=f"finishReason {self._finish}")
            return [*events, *self._usage_events(), failed]
        return [*events, *self._usage_events(), StreamEnd(stop_reason=stop)]

    def _usage_events(self) -> list[StreamEvent]:
        return [UsageReport(usage=_usage(self._usage), raw=self._usage)] if self._usage else []


_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


def gemini_failure(error: JSON) -> StreamFailure:
    code = error.get("code")
    status = code if isinstance(code, int) else None
    message = f"{error.get('status') or ''} {error.get('message') or error}".strip()
    retry_after: float | None = None
    for detail in as_array(error.get("details")):
        detail = as_object(detail) or {}
        delay = detail.get("retryDelay")
        match = _DELAY.match(delay) if isinstance(delay, str) else None
        if match:
            retry_after = float(match.group(1))
    retryable = status is None or status in RETRYABLE_STATUS
    return StreamFailure(
        f"stream error {status}: {message}",
        retryable=retryable,
        status=status,
        retry_after_s=retry_after,
    )


class GeminiProvider(StreamingHttpProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = GEMINI_BASE_URL,
        name: str = "gemini",
        caps: ModelCaps = DEFAULT_CAPS,
        generation: Mapping[str, Any] | None = None,
        **transport_policy: Any,
    ) -> None:
        super().__init__(name=name, caps=caps, **transport_policy)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._generation = dict(generation or {})

    def build_body(self, request: ModelRequest) -> JSON:
        return build_body(request, provider=self.name, generation=self._generation)

    def url(self, model: str) -> str:
        return f"{self._base_url}/models/{model}:streamGenerateContent?alt=sse"

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self._api_key:
            headers["x-goog-api-key"] = self._api_key
        return headers

    def assembler(self, model: str) -> GeminiStreamAssembler:
        return GeminiStreamAssembler(provider=self.name, model=model)

    def error_failure(self, error: JSON) -> StreamFailure:
        return gemini_failure(error)
