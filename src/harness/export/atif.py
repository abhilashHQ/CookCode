"""Projects a session log onto ATIF, the trajectory format Harbor reads."""

from collections.abc import Sequence
from typing import Any

from harness.core.events import (
    SCHEMA_VERSION,
    ApprovalResolved,
    BudgetExceeded,
    ErrorEvent,
    Event,
    Interrupted,
    ItemEvent,
    ModelRequestFinished,
    ModelRequestStarted,
    ProviderRetry,
    Repaired,
    RunConfig,
    RunStarted,
    SessionFinished,
    SessionStarted,
    SteerMessage,
    ToolCallFinished,
    UserMessage,
)
from harness.core.items import Message, Reasoning, ToolCall, ToolResult
from harness.core.usage import Usage

ATIF_VERSION = "ATIF-v1.8"
AGENT_NAME = "harness"
REASONING_TEXT_FIELDS = ("reasoning_content", "reasoning")

Json = dict[str, Any]


def _clean(value: Json) -> Json:
    return {key: item for key, item in value.items() if item is not None and item != {}}


def _reasoning_text(item: Reasoning) -> str:
    if item.summary_text:
        return item.summary_text
    for name in REASONING_TEXT_FIELDS:
        text = item.payload.get(name)
        if isinstance(text, str):
            return text
    return ""


def _system_note(body: Interrupted | BudgetExceeded | ErrorEvent) -> str:
    if isinstance(body, Interrupted):
        killed = f", killed {', '.join(body.killed)}" if body.killed else ""
        return f"interrupted while {body.phase}{killed}"
    if isinstance(body, BudgetExceeded):
        return f"budget exceeded: {body.limit} reached {body.value:g}"
    retry = "retryable" if body.retryable else "not retryable"
    return f"{body.kind} error ({retry}): {body.message}"


class _Builder:
    def __init__(self) -> None:
        self.steps: list[Json] = []
        self.current: Json | None = None
        self.call_steps: dict[str, Json] = {}
        self.provider_ids: dict[str, str] = {}
        self.calls: dict[str, Json] = {}
        self.finished: dict[str, ToolCallFinished] = {}
        self.repaired: dict[str, str] = {}
        self.usage = Usage()
        self.config: RunConfig | None = None
        self.config_id: str | None = None
        self.status: str | None = None
        self.runs = 0

    def step(self, event: Event, source: str, message: str, **fields: Any) -> Json:
        step = {"timestamp": event.ts.isoformat(), "source": source, "message": message, **fields}
        self.steps.append(step)
        return step

    def add(self, event: Event) -> None:
        body = event.body
        if isinstance(body, SessionStarted | RunStarted):
            self.config, self.config_id = body.config, body.config_id
            self.runs += isinstance(body, RunStarted)
        elif isinstance(body, UserMessage | SteerMessage):
            self.current = None
            extra = {"steer": True} if isinstance(body, SteerMessage) else None
            self.step(event, "user", body.text, extra=extra)
        elif isinstance(body, ModelRequestStarted):
            model = self.config.model if self.config is not None else None
            self.current = self.step(
                event,
                "agent",
                "",
                model_name=model,
                llm_call_count=1,
                extra={"request_id": body.request_id},
            )
        elif isinstance(body, ProviderRetry) and self.current is not None:
            extra = self.current["extra"]
            extra["retries"] = extra.get("retries", 0) + 1
        elif isinstance(body, ModelRequestFinished) and self.current is not None:
            self.finish_request(body)
        elif isinstance(body, ItemEvent):
            self.item(event, body)
        elif isinstance(body, ApprovalResolved) and body.call_id in self.calls:
            approval = _clean({"decision": body.decision, "rule": body.rule, "reason": body.reason})
            self.calls[body.call_id].setdefault("extra", {})["approval"] = approval
        elif isinstance(body, ToolCallFinished):
            self.finished[body.call_id] = body
        elif isinstance(body, Repaired):
            self.repaired[body.call_id] = body.reason
        elif isinstance(body, Interrupted | BudgetExceeded | ErrorEvent):
            self.current = None
            self.step(event, "system", _system_note(body))
        elif isinstance(body, SessionFinished):
            self.status = body.status

    def finish_request(self, body: ModelRequestFinished) -> None:
        assert self.current is not None
        usage = body.usage
        self.usage += usage
        self.current["metrics"] = _clean(
            {
                "prompt_tokens": usage.total_input,
                "completion_tokens": usage.output,
                "cached_tokens": usage.cache_read,
                "extra": _clean(
                    {
                        "cache_write_tokens": usage.cache_write or None,
                        "reasoning_tokens": usage.reasoning or None,
                    }
                ),
            }
        )
        self.current["extra"]["stop_reason"] = body.stop_reason

    def item(self, event: Event, body: ItemEvent) -> None:
        item = body.item
        step = self.current
        if isinstance(item, ToolResult):
            self.result(event, item)
        elif step is None:
            return
        elif isinstance(item, Message) and item.role == "assistant":
            step["message"] += item.text
        elif isinstance(item, Reasoning):
            text = _reasoning_text(item)
            if text:
                step["reasoning_content"] = step.get("reasoning_content", "") + text
        elif isinstance(item, ToolCall):
            call: Json = {
                "tool_call_id": item.provider_call_id,
                "function_name": item.name,
                "arguments": item.arguments if item.raw_arguments is None else {},
            }
            if item.raw_arguments is not None:
                call["extra"] = {"raw_arguments": item.raw_arguments}
            step.setdefault("tool_calls", []).append(call)
            self.calls[item.id] = call
            self.call_steps[item.id] = step
            self.provider_ids[item.id] = item.provider_call_id

    def result(self, event: Event, item: ToolResult) -> None:
        finished = self.finished.get(item.call_id)
        extra = _clean(
            {
                "status": item.status,
                "exit_status": finished.exit_status if finished is not None else None,
                "duration_s": round(finished.duration_s, 3) if finished is not None else None,
                "repaired": self.repaired.get(item.call_id),
            }
        )
        step = self.call_steps.get(item.call_id)
        result: Json = {
            "source_call_id": self.provider_ids.get(item.call_id),
            "content": item.content,
            "extra": extra,
        }
        if step is None:
            step = self.step(event, "system", "tool result without a matching call")
            result["source_call_id"] = None
        step.setdefault("observation", {"results": []})["results"].append(_clean(result))

    def trajectory(self, session_id: str | None, tool_definitions: list[Json] | None) -> Json:
        if not self.steps:
            raise ValueError("the log has no messages to export")
        for index, step in enumerate(self.steps, start=1):
            step["step_id"] = index
        config = self.config
        agent = _clean(
            {
                "name": AGENT_NAME,
                "version": config.harness_version if config is not None else "unknown",
                "model_name": config.model if config is not None else None,
                "tool_definitions": tool_definitions,
                "extra": _clean(
                    {
                        "provider": config.provider if config is not None else None,
                        "config_id": self.config_id,
                        "mode": config.mode if config is not None else None,
                        "policy": list(config.policy) if config is not None else None,
                        "system_prompt_sha256": (
                            config.system_prompt_sha256 if config is not None else None
                        ),
                    }
                ),
            }
        )
        return _clean(
            {
                "schema_version": ATIF_VERSION,
                "session_id": session_id,
                "agent": agent,
                "steps": [_clean(step) for step in self.steps],
                "final_metrics": {
                    "total_prompt_tokens": self.usage.total_input,
                    "total_completion_tokens": self.usage.output,
                    "total_cached_tokens": self.usage.cache_read,
                    "total_steps": len(self.steps),
                },
                "extra": _clean(
                    {
                        "harness_schema_version": SCHEMA_VERSION,
                        "status": self.status,
                        "runs": self.runs,
                    }
                ),
            }
        )


def to_atif(
    events: Sequence[Event],
    *,
    session_id: str | None = None,
    tool_definitions: list[Json] | None = None,
) -> Json:
    builder = _Builder()
    for event in events:
        builder.add(event)
    return builder.trajectory(session_id, tool_definitions)
