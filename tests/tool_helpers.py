from pathlib import Path
from typing import Any

from pydantic import BaseModel

from harness.agent.tools import Tool, ToolContext, ToolOutput
from harness.core.cancel import CancelToken
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.env.base import Environment
from harness.env.local import LocalEnvironment
from harness.kit import Toolkit, toolkit


def make_ctx(
    root: Path,
    *,
    cwd: str = ".",
    cancel: CancelToken | None = None,
    timeout_s: float = 10.0,
    max_output_bytes: int = 30_000,
) -> ToolContext:
    return ToolContext(
        env=LocalEnvironment(root),
        cancel=cancel or CancelToken(),
        cwd=cwd,
        timeout_s=timeout_s,
        max_output_bytes=max_output_bytes,
    )


async def run_tool(tool: Tool[Any], ctx: ToolContext, **arguments: Any) -> ToolOutput:
    args: BaseModel = tool.args_model.model_validate(arguments)
    return await tool.handler(args, ctx)


async def make_kit(features: Features, env: Environment, memory: Path) -> Toolkit:
    return await toolkit(Strategy(), features, env, home=memory / "home", memory_root=memory)
