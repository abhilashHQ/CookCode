"""Counterfactual context ablation: rebuild every logged request under another strategy.

The trajectory is fixed (the model's replies are the logged ones), so this measures what each
strategy would have sent, not how the model would have behaved with it. Summaries need model
calls, so only strategies that the log can rebuild alone (raw and masking) apply.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from harness.agent.context import (
    SUMMARIZE_VIEW,
    build_request,
    estimate_tokens,
    shared_prefix_tokens,
)
from harness.core.events import Event, ModelRequestStarted, RunConfig, RunStarted, SessionStarted
from harness.core.items import Item
from harness.core.strategy import Strategy


@dataclass(frozen=True)
class ContextCost:
    requests: int = 0
    input_tokens: int = 0
    cacheable_tokens: int = 0
    masked_requests: int = 0

    @property
    def cacheable_share(self) -> float:
        return self.cacheable_tokens / self.input_tokens if self.input_tokens else 0.0

    def __add__(self, other: "ContextCost") -> "ContextCost":
        return ContextCost(
            self.requests + other.requests,
            self.input_tokens + other.input_tokens,
            self.cacheable_tokens + other.cacheable_tokens,
            self.masked_requests + other.masked_requests,
        )


def session_cost(events: Sequence[Event], strategy: Strategy) -> ContextCost:
    """Estimated input tokens of every agent request under `strategy`, and how many of them a
    prefix cache could serve: the previous request's shared leading items. System prompt and
    tools are equal across strategies and left out of both numbers."""
    if strategy.summarizes:
        raise ValueError(f"{strategy.label} needs model calls; only raw and masking replay")
    config: RunConfig | None = None
    previous: tuple[Item, ...] = ()
    cost = ContextCost()
    for index, event in enumerate(events):
        body = event.body
        if isinstance(body, SessionStarted | RunStarted):
            config = body.config
        elif isinstance(body, ModelRequestStarted) and body.view != SUMMARIZE_VIEW:
            request = build_request(
                events[:index],
                model=config.model if config is not None else "",
                system="",
                tools=(),
                provider=config.provider if config is not None else None,
                strategy=strategy,
            )
            raw = build_request(
                events[:index],
                model=config.model if config is not None else "",
                system="",
                tools=(),
                provider=config.provider if config is not None else None,
            )
            tokens = estimate_tokens(request)
            cost = cost + ContextCost(
                requests=1,
                input_tokens=tokens,
                cacheable_tokens=shared_prefix_tokens(previous, request.items),
                masked_requests=int(request.items != raw.items),
            )
            previous = request.items
    return cost


def render(rows: Sequence[tuple[str, ContextCost]]) -> str:
    base = rows[0][1].input_tokens if rows else 0
    lines = [f"{'strategy':<24} {'requests':>8} {'input tokens':>13} {'vs first':>9} "
             f"{'cacheable':>10} {'uncached':>11}"]
    for label, cost in rows:
        delta = cost.input_tokens / base - 1 if base else 0.0
        uncached = cost.input_tokens - cost.cacheable_tokens
        lines.append(
            f"{label:<24} {cost.requests:>8} {cost.input_tokens:>13,} {delta:>+9.1%} "
            f"{cost.cacheable_share:>10.1%} {uncached:>11,}"
        )
    return "\n".join(lines)
