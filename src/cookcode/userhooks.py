"""User hooks: shell commands from settings (see cookcode.config) that run before tool calls or
before the turn ends:

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

from cookcode.agent.hooks import Hook, HookContext
from cookcode.config import HookSpec, Settings
from cookcode.core.events import ItemEvent
from cookcode.core.items import Message, ToolCall

BLOCK_EXIT = 2


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


def user_hook(spec: HookSpec) -> Hook:
    name = f"hook:{spec.command}"[:120]
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


def user_hooks(settings: Settings) -> list[Hook]:
    return [user_hook(spec) for spec in settings.hooks]
