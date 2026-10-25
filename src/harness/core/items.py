from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Tag, TypeAdapter

from harness.core._union import tag_or_fallback

Role = Literal["user", "assistant", "system"]
ToolResultStatus = Literal["ok", "error", "interrupted", "denied", "unknown"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class TextPart(_Frozen):
    type: Literal["text"] = "text"
    text: str


class Message(_Frozen):
    type: Literal["message"] = "message"
    role: Role
    parts: tuple[TextPart, ...]

    @property
    def text(self) -> str:
        return "".join(part.text for part in self.parts)


class Reasoning(_Frozen):
    type: Literal["reasoning"] = "reasoning"
    provider: str
    model_family: str
    payload: dict[str, Any]
    summary_text: str | None = None

    def replayable_to(self, provider: str, model_family: str) -> bool:
        return self.provider == provider and self.model_family == model_family


class ToolCall(_Frozen):
    type: Literal["tool_call"] = "tool_call"
    id: str
    provider_call_id: str
    provider_item_id: str | None = None
    name: str
    arguments: dict[str, Any]


class ToolResult(_Frozen):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    status: ToolResultStatus
    content: str


class OtherItem(_Frozen):
    type: Literal["other"] = "other"
    provider: str
    raw: dict[str, Any]


class UnknownItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    type: str


ITEM_TYPES = frozenset({"message", "reasoning", "tool_call", "tool_result", "other"})

Item = Annotated[
    Annotated[Message, Tag("message")]
    | Annotated[Reasoning, Tag("reasoning")]
    | Annotated[ToolCall, Tag("tool_call")]
    | Annotated[ToolResult, Tag("tool_result")]
    | Annotated[OtherItem, Tag("other")]
    | Annotated[UnknownItem, Tag("unknown")],
    Discriminator(tag_or_fallback(ITEM_TYPES, "unknown")),
]

ITEM_ADAPTER: TypeAdapter[Item] = TypeAdapter(Item)


def text_message(role: Role, text: str) -> Message:
    return Message(role=role, parts=(TextPart(text=text),))
