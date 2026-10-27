"""Composition: the tools, hooks and system prompt additions a run gets from its strategy and
features. Every composition site (CLI, suite runner, Harbor) builds its agent from this."""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from harness.agent.hooks import Hook
from harness.agent.tools import ToolRegistry
from harness.config import Settings
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.env.base import Environment
from harness.mcp import McpHub, tool_search
from harness.memory.layer import memory_parts
from harness.skills import load_skills, skill_tool, skills_profile
from harness.subagents import Spawner, task_tool
from harness.tools import default_tools
from harness.tools.verify import repro_tool, verify_first_hook
from harness.userhooks import user_hooks


@dataclass(frozen=True)
class Toolkit:
    tools: ToolRegistry
    hooks: tuple[Hook, ...]
    profile: str = ""
    hub: McpHub = field(default_factory=McpHub)

    async def aclose(self) -> None:
        await self.hub.aclose()

    def system(self, prompt: str) -> str:
        """The system prompt with memory and skills sections, fixed for the whole session."""
        return f"{prompt}\n\n{self.profile}" if self.profile else prompt


async def toolkit(
    strategy: Strategy,
    features: Features,
    env: Environment,
    *,
    home: Path,
    memory_root: Path,
    settings: Settings | None = None,
    spawner: Spawner | None = None,
) -> Toolkit:
    """`home` holds user-level skills; evals point it at an empty directory so nothing from the
    machine leaks into a run. `settings` are the merged settings files. `spawner` is required
    with sub-agents."""
    tools = default_tools(recall=strategy.masks, jobs=features.jobs)
    hooks: list[Hook] = []
    if features.verify_first:
        tools.append(repro_tool())
        hooks.append(verify_first_hook())
    memory = await memory_parts(features, env, memory_root)
    tools += memory.tools
    hooks += memory.hooks
    sections = [memory.profile] if memory.profile else []
    if features.skills:
        skills = await load_skills(env, home / "skills")
        if skills:
            tools.append(skill_tool(skills))
            sections.append(skills_profile(skills))
    shared: list[Hook] = []
    if features.hooks:
        shared = user_hooks(settings or Settings())
        hooks += shared
    hub = McpHub()
    servers = (settings or Settings()).mcp.servers
    if features.mcp and servers:
        await hub.start(servers, env.root if os.path.isdir(env.root) else os.getcwd())
        deferred = hub.tools()
        tools += deferred
        if deferred:
            tools.append(tool_search(deferred))
        if hub.profile():
            sections.append(hub.profile())
    profile = "\n\n".join(sections)
    if features.subagents:
        if spawner is None:
            raise ValueError("the subagents feature needs a spawner")
        child = replace(spawner, system_prompt=Toolkit(ToolRegistry([]), (), profile).system(
            spawner.system_prompt))
        tools.append(task_tool(child, tools, shared))
    return Toolkit(ToolRegistry(tools), tuple(hooks), profile, hub)
