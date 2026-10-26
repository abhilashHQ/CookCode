import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    model_validator,
)

from harness.core._union import tag_or_fallback
from harness.core.ids import new_id, utc_now
from harness.core.items import Item
from harness.core.usage import StopReason, Usage

SCHEMA_VERSION = 1

Layer = Literal["model", "internal"]
SideEffect = Literal["read_only", "mutating", "external"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Limits(_Frozen):
    max_turns: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_s: float | None = None


class RunConfig(_Frozen):
    provider: str
    model: str
    effort: str | None = None
    tools: tuple[str, ...] = ()
    system_prompt_sha256: str = ""
    harness_version: str
    limits: Limits = Limits()
    mode: str | None = None
    policy: tuple[str, ...] = ()


def compute_config_id(config: RunConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class _Body(_Frozen):
    LAYER: ClassVar[Layer] = "internal"


class SessionStarted(_Body):
    type: Literal["session_started"] = "session_started"
    config: RunConfig
    config_id: str

    @classmethod
    def for_config(cls, config: RunConfig) -> "SessionStarted":
        return cls(config=config, config_id=compute_config_id(config))


class RunStarted(_Body):
    type: Literal["run_started"] = "run_started"
    config: RunConfig
    config_id: str

    @classmethod
    def for_config(cls, config: RunConfig) -> "RunStarted":
        return cls(config=config, config_id=compute_config_id(config))


class UserMessage(_Body):
    LAYER: ClassVar[Layer] = "model"
    type: Literal["user_message"] = "user_message"
    text: str


class SteerMessage(_Body):
    LAYER: ClassVar[Layer] = "model"
    type: Literal["steer_message"] = "steer_message"
    text: str


class ModelRequestStarted(_Body):
    type: Literal["model_request_started"] = "model_request_started"
    request_id: str
    view: str
    token_estimate: int
    items_sha256: str | None = None
    reasoning_dropped: int = 0


class ItemEvent(_Body):
    LAYER: ClassVar[Layer] = "model"
    type: Literal["item"] = "item"
    item: Item
    request_id: str | None = None


class ModelRequestFinished(_Body):
    type: Literal["model_request_finished"] = "model_request_finished"
    request_id: str
    stop_reason: StopReason
    usage: Usage
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    raw_ref: str | None = None


class ProviderRetry(_Body):
    type: Literal["provider_retry"] = "provider_retry"
    request_id: str
    attempt: int
    delay_s: float
    reason: str
    status: int | None = None


class ToolCallStarted(_Body):
    type: Literal["tool_call_started"] = "tool_call_started"
    call_id: str
    tool: str
    arguments: dict[str, Any]
    side_effect: SideEffect


class ToolCallFinished(_Body):
    type: Literal["tool_call_finished"] = "tool_call_finished"
    call_id: str
    exit_status: Literal["ok", "error", "timeout", "killed"]
    duration_s: float
    output_bytes: int
    truncated: bool
    cwd: str | None = None


class ApprovalRequested(_Body):
    type: Literal["approval_requested"] = "approval_requested"
    call_id: str
    risk: SideEffect
    rule: str | None = None


class ApprovalResolved(_Body):
    type: Literal["approval_resolved"] = "approval_resolved"
    call_id: str
    decision: Literal["allow", "deny", "allow_always"]
    reason: str | None = None
    rule: str | None = None


class CheckpointCreated(_Body):
    type: Literal["checkpoint_created"] = "checkpoint_created"
    checkpoint_id: str
    paths: tuple[str, ...]
    turn: int


class CheckpointRestored(_Body):
    LAYER: ClassVar[Layer] = "model"
    type: Literal["checkpoint_restored"] = "checkpoint_restored"
    checkpoint_ids: tuple[str, ...]
    paths: tuple[str, ...]
    turns: tuple[int, ...]


class CompactionApplied(_Body):
    type: Literal["compaction_applied"] = "compaction_applied"
    strategy: str
    from_seq: int
    to_seq: int
    tokens_before: int
    tokens_after: int


class Interrupted(_Body):
    type: Literal["interrupted"] = "interrupted"
    phase: Literal["streaming", "tool", "idle"]
    killed: tuple[str, ...] = ()


class Repaired(_Body):
    type: Literal["repaired"] = "repaired"
    call_id: str
    status: Literal["interrupted", "unknown"]
    reason: str


class BudgetExceeded(_Body):
    type: Literal["budget_exceeded"] = "budget_exceeded"
    limit: Literal["turns", "tokens", "cost", "wall_clock"]
    value: float


class ErrorEvent(_Body):
    type: Literal["error"] = "error"
    kind: Literal["provider", "tool", "environment", "internal"]
    retryable: bool
    message: str


class SessionFinished(_Body):
    type: Literal["session_finished"] = "session_finished"
    status: Literal["done", "error", "budget_exceeded", "interrupted"]
    turns: int
    usage: Usage


class OtherEvent(_Body):
    model_config = ConfigDict(frozen=True, extra="allow")
    type: str


EVENT_TYPES = frozenset({
    "session_started", "run_started", "user_message", "steer_message", "model_request_started",
    "item", "model_request_finished", "tool_call_started", "tool_call_finished",
    "approval_requested", "approval_resolved", "checkpoint_created", "checkpoint_restored",
    "compaction_applied", "interrupted", "repaired", "budget_exceeded", "error",
    "session_finished", "provider_retry",
})

EventBody = Annotated[
    Annotated[SessionStarted, Tag("session_started")]
    | Annotated[RunStarted, Tag("run_started")]
    | Annotated[UserMessage, Tag("user_message")]
    | Annotated[SteerMessage, Tag("steer_message")]
    | Annotated[ModelRequestStarted, Tag("model_request_started")]
    | Annotated[ItemEvent, Tag("item")]
    | Annotated[ModelRequestFinished, Tag("model_request_finished")]
    | Annotated[ProviderRetry, Tag("provider_retry")]
    | Annotated[ToolCallStarted, Tag("tool_call_started")]
    | Annotated[ToolCallFinished, Tag("tool_call_finished")]
    | Annotated[ApprovalRequested, Tag("approval_requested")]
    | Annotated[ApprovalResolved, Tag("approval_resolved")]
    | Annotated[CheckpointCreated, Tag("checkpoint_created")]
    | Annotated[CheckpointRestored, Tag("checkpoint_restored")]
    | Annotated[CompactionApplied, Tag("compaction_applied")]
    | Annotated[Interrupted, Tag("interrupted")]
    | Annotated[Repaired, Tag("repaired")]
    | Annotated[BudgetExceeded, Tag("budget_exceeded")]
    | Annotated[ErrorEvent, Tag("error")]
    | Annotated[SessionFinished, Tag("session_finished")]
    | Annotated[OtherEvent, Tag("other")],
    Discriminator(tag_or_fallback(EVENT_TYPES, "other")),
]


class Event(_Frozen):
    seq: int = Field(ge=0)
    id: str
    ts: datetime
    schema_version: int = SCHEMA_VERSION
    layer: Layer
    body: EventBody

    @model_validator(mode="after")
    def _layer_matches_body(self) -> "Event":
        if not isinstance(self.body, OtherEvent) and self.layer != type(self.body).LAYER:
            raise ValueError(f"layer {self.layer!r} does not match body type {self.body.type!r}")
        return self


def make_event(seq: int, body: EventBody) -> Event:
    return Event(seq=seq, id=new_id(), ts=utc_now(), layer=type(body).LAYER, body=body)


def to_json_line(event: Event) -> str:
    return event.model_dump_json()


def from_json_line(line: str) -> Event:
    return Event.model_validate_json(line)


def event_schema() -> dict[str, Any]:
    schema = TypeAdapter(Event).json_schema(mode="serialization")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"harness-events-v{SCHEMA_VERSION}",
        **schema,
    }
