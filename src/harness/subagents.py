"""Sub-agents: the `task` tool runs a child agent on a delegated job and returns its report.

The child has its own session log in the same store and its own context, so whatever it reads
costs the parent only the report. It gets the parent's tools minus `task` (no recursion), the
parent's mode, policy and approver, and runs in the same environment, so its edits are real
edits. `task` is a mutating tool: calls run one at a time, and a child cut off by a crash is
`unknown` on resume, like any mutating call.

The return contract is a report with four sections, enforced by a stop hook on the child (it
is sent back while the sections are missing) and capped before it reaches the parent."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from harness.agent.approval import Approver
from harness.agent.gate import Mode, Policy
from harness.agent.hooks import Hook, HookContext
from harness.agent.loop import Agent, AgentConfig
from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.events import ItemEvent, Limits
from harness.core.ids import new_id
from harness.core.items import Message
from harness.providers.base import Provider
from harness.store.base import CheckpointStore, SessionStore

SECTIONS = ("Summary", "Files", "Evidence", "Open questions")
REPORT_CHARS = 8000
CHILD_MAX_TURNS = 25
REPORT_HOOK = "report"

DESCRIPTION = (
    "Delegate a self-contained job to a sub-agent with its own context: exploring a large part "
    "of the code, tracing a bug through many files, or a contained change. It has your tools "
    "and edits the same files. You get back its report (summary, files read or changed, "
    "evidence, open questions), not its transcript, so give it everything it needs in prompt."
)
CHILD_PROMPT = """

You are a sub-agent: another agent delegated one job to you and sees only your final message. \
End with a report in exactly this form:

## Summary
What you found or did, in a few sentences.
## Files
- path: read or changed, and why it matters
## Evidence
- the command you ran or the line you read, and what it showed
## Open questions
- anything unresolved, or "none\""""


class TaskArgs(BaseModel):
    prompt: str = Field(description="The job, with all the context the sub-agent needs.")


def missing_sections(text: str) -> list[str]:
    return [s for s in SECTIONS if not re.search(rf"^#+\s*{re.escape(s)}\s*$", text, re.M | re.I)]


def _final_text(ctx: HookContext) -> str:
    for event in reversed(ctx.history):
        body = event.body
        if isinstance(body, ItemEvent) and isinstance(body.item, Message):
            return body.item.text if body.item.role == "assistant" else ""
    return ""


async def _report_check(ctx: HookContext) -> str | None:
    missing = missing_sections(_final_text(ctx))
    if not missing:
        return None
    return (
        f"your final message lacks the report sections {', '.join(missing)}. End with the "
        "report described in your instructions."
    )


@dataclass(frozen=True)
class Spawner:
    """What a child agent shares with its parent."""

    provider: Provider
    store: SessionStore
    model: str
    system_prompt: str
    mode: Mode
    policy: Policy
    approver: Approver | None = None
    checkpoints: CheckpointStore | None = None


def task_tool(
    spawner: Spawner, tools: Sequence[Tool[Any]], hooks: Sequence[Hook]
) -> Tool[TaskArgs]:
    child_tools = [tool for tool in tools if tool.name != "task"]
    child_hooks = (*hooks, Hook(name=REPORT_HOOK, before_stop=_report_check))

    async def run(args: TaskArgs, ctx: ToolContext) -> ToolOutput:
        session_id = new_id()
        log = spawner.store.open(session_id, create=True)
        try:
            agent = Agent(
                log=log,
                provider=spawner.provider,
                env=ctx.env,
                tools=ToolRegistry(child_tools),
                config=AgentConfig(
                    model=spawner.model,
                    system_prompt=spawner.system_prompt + CHILD_PROMPT,
                    limits=Limits(max_turns=CHILD_MAX_TURNS),
                    cwd=ctx.cwd,
                    mode=spawner.mode,
                    policy=spawner.policy,
                ),
                approver=spawner.approver,
                checkpoints=spawner.checkpoints,
                hooks=child_hooks,
            )
            outcome = await agent.run(args.prompt, cancel=ctx.cancel)
            events = log.read()
        finally:
            log.close()
        report = ""
        for event in reversed(events):
            body = event.body
            if isinstance(body, ItemEvent) and isinstance(body.item, Message):
                report = body.item.text if body.item.role == "assistant" else ""
                break
        capped = report if len(report) <= REPORT_CHARS else (
            report[:REPORT_CHARS] + f"\n[report cut at {REPORT_CHARS} characters]"
        )
        missing = missing_sections(report)
        tokens = outcome.usage.total_input + outcome.usage.output
        footer = (
            f"\n[sub-agent session {session_id}: {outcome.status}, {outcome.turns} turns, "
            f"{tokens} tokens"
            + (f"; report lacks {', '.join(missing)}" if missing else "")
            + "]"
        )
        ok = outcome.status == "done" and not missing
        return ToolOutput(content=(capped or "(no report)") + footer,
                          status="ok" if ok else "error",
                          exit_status="ok" if ok else "error")

    return Tool(name="task", description=DESCRIPTION, args_model=TaskArgs,
                side_effect="mutating", handler=run)
