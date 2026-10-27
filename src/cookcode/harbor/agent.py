"""Harbor adapter: our loop runs on the host and acts inside the task container."""

# harbor ships no py.typed, so its annotations count as stubless; the gaps stay in this file.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

import json
import os
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from cookcode import __version__
from cookcode.agent.gate import Policy
from cookcode.agent.instructions import load_instructions, render_system
from cookcode.agent.loop import Agent, AgentConfig
from cookcode.agent.tools import ToolRegistry
from cookcode.cli.app import BASE_PROMPT, PRESETS, SECRET_ENV, UNATTENDED, make_provider
from cookcode.core.events import Event, Limits, ModelRequestFinished, SessionFinished
from cookcode.core.features import Features
from cookcode.core.ids import new_id
from cookcode.core.strategy import Strategy
from cookcode.core.usage import Usage
from cookcode.env.harbor import HarborEnvironment
from cookcode.export.atif import AGENT_NAME, to_atif
from cookcode.kit import toolkit
from cookcode.providers.base import Provider
from cookcode.redact import Redactor
from cookcode.store.checkpoints import FileCheckpointStore
from cookcode.store.jsonl import JsonlSessionStore
from cookcode.subagents import Spawner

PROMPT = BASE_PROMPT.replace("in a local repository", "inside a task container") + UNATTENDED


def split_model(model_name: str | None) -> tuple[str, str]:
    """`nim/minimaxai/minimax-m3.1-flash` names the preset first; a bare model uses nim."""
    if not model_name:
        return "nim", PRESETS["nim"][2]
    preset, sep, model = model_name.partition("/")
    if sep and preset in PRESETS:
        return preset, model
    return "nim", model_name


def total_usage(events: list[Event]) -> Usage:
    usage = Usage()
    for event in events:
        if isinstance(event.body, ModelRequestFinished):
            usage += event.body.usage
    return usage


class CookCodeAgent(BaseAgent):
    capabilities = AgentCapabilities(atif=True)

    def __init__(
        self,
        *args: Any,
        max_turns: int | str = 50,
        strategy: str = "raw",
        features: str = "base",
        provider: Provider | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, skip_model_parsing=True, **kwargs)
        self.max_turns = int(max_turns)
        self.strategy = Strategy.parse(strategy)
        self.features = Features.parse(features)
        self.provider = provider

    @staticmethod
    @override
    def name() -> str:
        return AGENT_NAME

    @override
    def version(self) -> str:
        return __version__

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        preset, model = split_model(self.model_name)
        base_url, key_env, _ = PRESETS[preset]
        workdir = await environment.exec("pwd")
        env = HarborEnvironment(environment, (workdir.stdout or "/").strip() or "/")
        secrets = {**os.environ, **self._extra_env}
        store = JsonlSessionStore(
            self.logs_dir / "sessions", Redactor.from_env(SECRET_ENV, secrets)
        )
        session_id = new_id()
        log = store.open(session_id, create=True)
        provider = self.provider or make_provider(
            preset, api_key=secrets.get(key_env), base_url=base_url
        )
        checkpoints = FileCheckpointStore(self.logs_dir / "checkpoints")
        tools = ToolRegistry([])
        try:
            instructions = await load_instructions(env, ".")
            system = render_system(PROMPT.format(root=env.root, cwd="."), instructions)
            spawner = Spawner(provider=provider, store=store, model=model, system_prompt=system,
                              mode="yolo", policy=Policy(), checkpoints=checkpoints)
            kit = await toolkit(
                self.strategy, self.features, env, home=self.logs_dir / "home",
                memory_root=self.logs_dir / "memory", spawner=spawner,
            )
            tools = kit.tools
            agent = Agent(
                log=log,
                provider=provider,
                env=env,
                tools=tools,
                config=AgentConfig(
                    model=model,
                    system_prompt=kit.system(system),
                    limits=Limits(max_turns=self.max_turns),
                    mode="yolo",
                    strategy=self.strategy,
                ),
                checkpoints=checkpoints,
                hooks=kit.hooks,
            )
            try:
                await agent.run(instruction)
            finally:
                await kit.aclose()
        finally:
            events = log.read()
            log.close()
            self.report(events, session_id, tools, context)

    def report(
        self, events: list[Event], session_id: str, tools: ToolRegistry, context: AgentContext
    ) -> None:
        usage = total_usage(events)
        context.n_input_tokens = usage.total_input
        context.n_cache_tokens = usage.cache_read
        context.n_output_tokens = usage.output
        finished = [e.body for e in events if isinstance(e.body, SessionFinished)]
        context.metadata = {
            "session_id": session_id,
            "status": finished[-1].status if finished else None,
        }
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": schema.name,
                    "description": schema.description,
                    "parameters": schema.parameters,
                },
            }
            for schema in tools.schemas()
        ]
        try:
            trajectory = to_atif(events, session_id=session_id, tool_definitions=definitions)
        except ValueError:
            return
        path = Path(self.logs_dir) / "trajectory.json"
        path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n")
