"""User hooks: shell commands from settings that run before tool calls or before the turn ends.

Settings come from the user's data directory and the repository's `.harness/settings.toml`:

    [[hooks]]
    point = "pre_tool"          # or "stop"
    match = "bash|write"        # regex on the tool name, pre_tool only, optional
    command = "python3 scripts/guard.py"
    timeout_s = 30

A hook gets the event as JSON on stdin and runs in the environment, at the repository root.
Exit 0 lets the loop go on; exit 2 blocks, with the hook's output as the message; any other
exit is a broken hook and is ignored, so a typo in a hook never wedges a session. Hooks are off
unless the hooks feature is on, since a repository's hooks run commands without asking."""

import base64
import json
import re
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from harness.agent.hooks import Hook, HookContext
from harness.core.events import ItemEvent
from harness.core.items import Message, ToolCall
from harness.env.base import Environment

SETTINGS = ".harness/settings.toml"
BLOCK_EXIT = 2


class HookSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    point: Literal["pre_tool", "stop"]
    command: str
    match: str | None = None
    timeout_s: float = Field(default=30, gt=0, le=600)

    @field_validator("match")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"match is not a valid regex: {exc}") from exc
        return value


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    hooks: tuple[HookSpec, ...] = ()


def parse_settings(text: str, source: str) -> Settings:
    try:
        return Settings.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(f"bad settings in {source}: {exc}") from exc


async def load_settings(env: Environment, user_file: Path) -> list[tuple[str, Settings]]:
    """User settings first, then the repository's."""
    found: list[tuple[str, Settings]] = []
    if user_file.is_file():
        found.append((str(user_file), parse_settings(user_file.read_text(), str(user_file))))
    try:
        text = (await env.read_file(SETTINGS)).decode()
    except (OSError, ValueError):
        return found
    found.append((SETTINGS, parse_settings(text, SETTINGS)))
    return found


def _last_message(ctx: HookContext) -> str:
    for event in reversed(ctx.history):
        body = event.body
        if isinstance(body, ItemEvent) and isinstance(body.item, Message):
            return body.item.text
    return ""


async def _run(spec: HookSpec, payload: dict[str, object], ctx: HookContext) -> str | None:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    result = await ctx.env.exec(
        f"printf %s {data} | base64 -d | ( {spec.command} )", cwd=".",
        timeout_s=spec.timeout_s, max_output_bytes=ctx.max_output_bytes, cancel=ctx.cancel,
    )
    if result.exit_code != BLOCK_EXIT:
        return None
    return result.output.strip() or f"blocked by hook: {spec.command}"


def user_hook(spec: HookSpec, source: str) -> Hook:
    name = f"hook:{source}:{spec.command}"[:120]
    pattern = re.compile(spec.match) if spec.match else None

    async def before_tool(call: ToolCall, ctx: HookContext) -> str | None:
        if pattern is not None and not pattern.fullmatch(call.name):
            return None
        payload: dict[str, object] = {"point": "pre_tool", "tool": call.name,
                                      "arguments": call.arguments, "cwd": ctx.cwd}
        return await _run(spec, payload, ctx)

    async def before_stop(ctx: HookContext) -> str | None:
        return await _run(spec, {"point": "stop", "last_message": _last_message(ctx)}, ctx)

    if spec.point == "pre_tool":
        return Hook(name=name, before_tool=before_tool)
    return Hook(name=name, before_stop=before_stop)


def user_hooks(settings: Sequence[tuple[str, Settings]]) -> list[Hook]:
    return [user_hook(spec, source) for source, found in settings for spec in found.hooks]
