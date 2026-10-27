"""Composition: the tools, hooks and memory profile a run gets from its strategy and features."""

from dataclasses import dataclass
from pathlib import Path

from harness.agent.hooks import Hook
from harness.agent.tools import ToolRegistry
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.env.base import Environment
from harness.memory.layer import memory_parts
from harness.tools import default_tools
from harness.tools.verify import repro_tool, verify_first_hook


@dataclass(frozen=True)
class Toolkit:
    tools: ToolRegistry
    hooks: tuple[Hook, ...]
    profile: str = ""

    def system(self, prompt: str) -> str:
        """The system prompt with the memory profile, fixed for the whole session."""
        return f"{prompt}\n\n{self.profile}" if self.profile else prompt


async def toolkit(
    strategy: Strategy, features: Features, env: Environment, memory_root: Path
) -> Toolkit:
    """The tools, hooks and memory profile a run gets from its context strategy and enabled
    features. Every composition site (CLI, suite runner, Harbor) builds its agent from this."""
    tools = default_tools(recall=strategy.masks, jobs=features.jobs)
    hooks: list[Hook] = []
    if features.verify_first:
        tools.append(repro_tool())
        hooks.append(verify_first_hook())
    memory = await memory_parts(features, env, memory_root)
    return Toolkit(ToolRegistry([*tools, *memory.tools]), (*hooks, *memory.hooks), memory.profile)
