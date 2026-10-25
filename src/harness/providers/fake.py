import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict

from harness.core.cancel import CancelToken
from harness.core.ids import new_id
from harness.core.items import Item, Message, ToolCall, text_message
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

DEFAULT_CAPS = ModelCaps(
    context_window=200_000, parallel_tool_calls=True, reasoning="none", prompt_caching=False
)


class FakeScriptExhausted(Exception):
    pass


class FakeTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[Item, ...]
    usage: Usage = Usage()
    stop_reason: StopReason | None = None
    fail_with: str | None = None


def say(text: str) -> Message:
    return text_message("assistant", text)


def call(name: str, provider_call_id: str | None = None, **arguments: Any) -> ToolCall:
    call_id = new_id()
    return ToolCall(
        id=call_id,
        provider_call_id=provider_call_id or f"call_{call_id[:12]}",
        name=name,
        arguments=arguments,
    )


def _default_stop(items: tuple[Item, ...]) -> StopReason:
    return "tool_use" if any(isinstance(item, ToolCall) for item in items) else "end_turn"


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        script: Iterable[FakeTurn],
        *,
        chunk_size: int = 8,
        event_delay_s: float = 0.0,
        caps: ModelCaps | None = None,
    ) -> None:
        self._script = list(script)
        self._next = 0
        self._chunk_size = chunk_size
        self._event_delay_s = event_delay_s
        self._caps = caps or DEFAULT_CAPS
        self.requests: list[ModelRequest] = []

    def capabilities(self, model: str) -> ModelCaps:
        return self._caps

    async def stream(
        self, request: ModelRequest, cancel: CancelToken
    ) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        if self._next >= len(self._script):
            raise FakeScriptExhausted(f"no scripted turn for request #{len(self.requests)}")
        turn = self._script[self._next]
        self._next += 1
        if turn.fail_with is not None:
            yield StreamEnd(stop_reason="error", error=turn.fail_with)
            return
        for index, item in enumerate(turn.items):
            if cancel.cancelled:
                yield StreamEnd(stop_reason="aborted")
                return
            yield ItemStarted(index=index, kind=item.type)
            if isinstance(item, Message):
                text = item.text
                for start in range(0, len(text), self._chunk_size):
                    await asyncio.sleep(self._event_delay_s)
                    if cancel.cancelled:
                        yield StreamEnd(stop_reason="aborted")
                        return
                    yield ItemDelta(index=index, text=text[start : start + self._chunk_size])
            await asyncio.sleep(self._event_delay_s)
            if cancel.cancelled:
                yield StreamEnd(stop_reason="aborted")
                return
            yield ItemDone(index=index, item=item)
        yield UsageReport(usage=turn.usage)
        yield StreamEnd(stop_reason=turn.stop_reason or _default_stop(turn.items))
