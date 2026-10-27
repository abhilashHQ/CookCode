"""Per-session numbers read straight from the log: tokens, cache share, time split, tool errors."""

import json
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from harness.core.events import (
    Event,
    ItemEvent,
    ModelRequestFinished,
    ModelRequestStarted,
    ProviderRetry,
    RunStarted,
    SessionStarted,
    ToolCallFinished,
)
from harness.core.items import ToolCall, ToolResult
from harness.core.usage import Usage
from harness.store.jsonl import JsonlSessionStore

REPEAT_STREAK = 3


class Price(BaseModel):
    """USD per million tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float = 0.0
    cache_read: float = 0.0
    output: float = 0.0


class Stats(BaseModel):
    model_config = ConfigDict(frozen=True)

    sessions: int = 0
    requests: int = 0
    retries: int = 0
    usage: Usage = Usage()
    tool_calls: int = 0
    tool_errors: int = 0
    repeat_streaks: int = 0
    model_s: float = 0.0
    tool_s: float = 0.0
    wall_s: float = 0.0
    cost_usd: float = 0.0
    estimated_input: int = 0
    measured_input: int = 0

    @property
    def calibration(self) -> float:
        """Provider-reported input tokens per estimated token, over requests that have both."""
        return self.measured_input / self.estimated_input if self.estimated_input else 0.0

    @property
    def cache_read_share(self) -> float:
        total = self.usage.total_input
        return self.usage.cache_read / total if total else 0.0

    @property
    def tool_error_rate(self) -> float:
        return self.tool_errors / self.tool_calls if self.tool_calls else 0.0

    def __add__(self, other: "Stats") -> "Stats":
        return Stats(
            sessions=self.sessions + other.sessions,
            requests=self.requests + other.requests,
            retries=self.retries + other.retries,
            usage=self.usage + other.usage,
            tool_calls=self.tool_calls + other.tool_calls,
            tool_errors=self.tool_errors + other.tool_errors,
            repeat_streaks=self.repeat_streaks + other.repeat_streaks,
            model_s=self.model_s + other.model_s,
            tool_s=self.tool_s + other.tool_s,
            wall_s=self.wall_s + other.wall_s,
            cost_usd=self.cost_usd + other.cost_usd,
            estimated_input=self.estimated_input + other.estimated_input,
            measured_input=self.measured_input + other.measured_input,
        )


def cost(usage: Usage, price: Price) -> float:
    fresh = usage.input + usage.cache_write
    return (
        fresh * price.input + usage.cache_read * price.cache_read + usage.output * price.output
    ) / 1_000_000


def repeat_streaks(calls: Sequence[tuple[str, str]]) -> int:
    """Runs of REPEAT_STREAK or more consecutive identical (call, result) pairs."""
    streaks = 0
    run = 1
    for index in range(1, len(calls) + 1):
        if index < len(calls) and calls[index] == calls[index - 1]:
            run += 1
            continue
        if run >= REPEAT_STREAK:
            streaks += 1
        run = 1
    return streaks


def session_stats(events: Sequence[Event], prices: dict[str, Price] | None = None) -> Stats:
    prices = prices or {}
    model = ""
    started: dict[str, Event] = {}
    calls: dict[str, ToolCall] = {}
    pairs: list[tuple[str, str]] = []
    usage = Usage()
    requests = retries = tool_calls = tool_errors = 0
    model_s = tool_s = cost_usd = 0.0
    estimated = measured = 0
    for event in events:
        body = event.body
        if isinstance(body, RunStarted | SessionStarted):
            model = body.config.model
        elif isinstance(body, ModelRequestStarted):
            started[body.request_id] = event
        elif isinstance(body, ModelRequestFinished):
            requests += 1
            usage += body.usage
            cost_usd += cost(body.usage, prices.get(model, Price()))
            begin = started.get(body.request_id)
            if begin is not None:
                model_s += (event.ts - begin.ts).total_seconds()
                if isinstance(begin.body, ModelRequestStarted) and body.usage.total_input:
                    estimated += begin.body.token_estimate
                    measured += body.usage.total_input
        elif isinstance(body, ProviderRetry):
            retries += 1
        elif isinstance(body, ToolCallFinished):
            tool_s += body.duration_s
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolCall):
            calls[body.item.id] = body.item
        elif isinstance(body, ItemEvent) and isinstance(body.item, ToolResult):
            result = body.item
            tool_calls += 1
            tool_errors += result.status == "error"
            call = calls.get(result.call_id)
            if call is not None:
                key = json.dumps([call.name, call.arguments], sort_keys=True)
                pairs.append((key, result.content))
    wall_s = (events[-1].ts - events[0].ts).total_seconds() if events else 0.0
    return Stats(
        sessions=1,
        requests=requests,
        retries=retries,
        usage=usage,
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        repeat_streaks=repeat_streaks(pairs),
        model_s=model_s,
        tool_s=tool_s,
        wall_s=wall_s,
        cost_usd=cost_usd,
        estimated_input=estimated,
        measured_input=measured,
    )


def session_files(root: Path) -> list[Path]:
    """Every session log under a directory: a suite run, a Harbor job or the data directory.
    All of them keep logs in a `sessions` directory, next to other JSONL like results."""
    return sorted(path for path in root.rglob("*.jsonl") if path.parent.name == "sessions")


def read_session(path: Path) -> list[Event]:
    return JsonlSessionStore(path.parent).read(path.stem)


def total(stats: Iterable[Stats]) -> Stats:
    result = Stats()
    for item in stats:
        result = result + item
    return result


def render(stats: Stats) -> str:
    usage = stats.usage
    return "\n".join(
        [
            f"sessions        {stats.sessions}",
            f"requests        {stats.requests} ({stats.retries} retries)",
            f"input tokens    {usage.total_input} ({stats.cache_read_share:.0%} cache reads)",
            f"output tokens   {usage.output} ({usage.reasoning} reasoning)",
            f"tool calls      {stats.tool_calls} ({stats.tool_error_rate:.0%} errors)",
            f"repeat streaks  {stats.repeat_streaks} (>= {REPEAT_STREAK} identical call+result)",
            f"time            {stats.wall_s:.0f}s wall, {stats.model_s:.0f}s model, "
            f"{stats.tool_s:.0f}s tools",
            f"cost            ${stats.cost_usd:.4f}",
            f"estimate scale  x{stats.calibration:.2f} (reported input / chars-over-4 estimate)",
        ]
    )
