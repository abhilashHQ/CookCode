from harness.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    BudgetExceeded,
    CheckpointCreated,
    CheckpointRestored,
    CompactionApplied,
    ErrorEvent,
    EventBody,
    HookBlocked,
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
    ToolCallStarted,
    UserMessage,
)
from harness.core.items import ToolCall
from harness.core.usage import Usage


def all_bodies() -> list[EventBody]:
    call = ToolCall(id="c1", provider_call_id="call_1", name="bash", arguments={"cmd": "ls"})
    return [
        SessionStarted.for_config(
            RunConfig(provider="fake", model="fake-1", harness_version="0.0.1")
        ),
        RunStarted.for_config(
            RunConfig(provider="fake", model="fake-1", harness_version="0.0.1")
        ),
        UserMessage(text="fix the failing test"),
        SteerMessage(text="also run ruff"),
        ModelRequestStarted(request_id="r1", view="raw", token_estimate=1200),
        ItemEvent(item=call, request_id="r1"),
        ModelRequestFinished(
            request_id="r1", stop_reason="tool_use", usage=Usage(input=1000, output=50)
        ),
        ProviderRetry(request_id="r1", attempt=1, delay_s=2.0, reason="HTTP 429", status=429),
        ToolCallStarted(
            call_id="c1", tool="bash", arguments={"cmd": "ls"}, side_effect="read_only"
        ),
        ToolCallFinished(
            call_id="c1",
            exit_status="ok",
            duration_s=0.12,
            output_bytes=42,
            truncated=False,
            cwd="src",
        ),
        ApprovalRequested(call_id="c1", risk="read_only", rule=None),
        ApprovalResolved(call_id="c1", decision="allow", reason=None, rule="bash:ls*"),
        CheckpointCreated(checkpoint_id="k1", paths=("src/app.py",), turn=1),
        CheckpointRestored(checkpoint_ids=("k1",), paths=("src/app.py",), turns=(1,)),
        CompactionApplied(
            strategy="mask", from_seq=3, to_seq=40, tokens_before=90000, tokens_after=30000
        ),
        HookBlocked(hook="verify-first", point="stop", message="repro still fails"),
        Interrupted(phase="tool", killed=("bash:c1",)),
        Repaired(call_id="c1", status="unknown", reason="started, never finished"),
        BudgetExceeded(limit="turns", value=50),
        ErrorEvent(kind="provider", retryable=True, message="429 rate limited"),
        SessionFinished(status="done", turns=3, usage=Usage(input=3000, output=200)),
    ]
