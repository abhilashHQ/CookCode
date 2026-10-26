from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from harness.core.cancel import CancelToken
from harness.core.items import Item
from harness.core.usage import StopReason, Usage


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ToolSchema(_Frozen):
    name: str
    description: str
    parameters: dict[str, Any]


class ModelRequest(_Frozen):
    model: str
    system: str
    tools: tuple[ToolSchema, ...] = ()
    items: tuple[Item, ...]
    max_output_tokens: int | None = None


class ModelCaps(_Frozen):
    context_window: int
    parallel_tool_calls: bool
    reasoning: Literal["none", "opaque", "text"]
    prompt_caching: bool


class ItemStarted(_Frozen):
    type: Literal["item_started"] = "item_started"
    index: int
    kind: str


class ItemDelta(_Frozen):
    type: Literal["item_delta"] = "item_delta"
    index: int
    text: str


class ItemDone(_Frozen):
    type: Literal["item_done"] = "item_done"
    index: int
    item: Item


class UsageReport(_Frozen):
    type: Literal["usage"] = "usage"
    usage: Usage
    raw: dict[str, Any] = Field(default_factory=dict)


class StreamRetry(_Frozen):
    type: Literal["retry"] = "retry"
    attempt: int
    delay_s: float
    reason: str
    status: int | None = None


class StreamEnd(_Frozen):
    type: Literal["stream_end"] = "stream_end"
    stop_reason: StopReason
    error: str | None = None


StreamEvent = ItemStarted | ItemDelta | ItemDone | UsageReport | StreamRetry | StreamEnd


class Provider(Protocol):
    name: str

    def capabilities(self, model: str) -> ModelCaps: ...

    def stream(self, request: ModelRequest, cancel: CancelToken) -> AsyncIterator[StreamEvent]: ...
