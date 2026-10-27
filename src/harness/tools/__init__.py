from dataclasses import dataclass
from typing import Any

from harness.agent.hooks import Hook
from harness.agent.tools import Tool, ToolRegistry
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.tools.bash import bash_tool
from harness.tools.files import edit_tool, read_tool, write_tool
from harness.tools.jobs import job_tools
from harness.tools.recall import recall_tool
from harness.tools.search import glob_tool, grep_tool
from harness.tools.verify import repro_tool, verify_first_hook


def default_tools(*, recall: bool = False, jobs: bool = False) -> list[Tool[Any]]:
    """recall comes with strategies that mask tool outputs; jobs with the jobs feature."""
    tools: list[Tool[Any]] = [
        read_tool(), write_tool(), edit_tool(), grep_tool(), glob_tool(), bash_tool()
    ]
    if jobs:
        tools += job_tools()
    return [*tools, recall_tool()] if recall else tools


@dataclass(frozen=True)
class Toolkit:
    tools: ToolRegistry
    hooks: tuple[Hook, ...]


def toolkit(strategy: Strategy, features: Features) -> Toolkit:
    """The tools and hooks a run gets from its context strategy and enabled features. Every
    composition site (CLI, suite runner, Harbor) builds its agent from this."""
    tools = default_tools(recall=strategy.masks, jobs=features.jobs)
    hooks: list[Hook] = []
    if features.verify_first:
        tools.append(repro_tool())
        hooks.append(verify_first_hook())
    return Toolkit(ToolRegistry(tools), tuple(hooks))
