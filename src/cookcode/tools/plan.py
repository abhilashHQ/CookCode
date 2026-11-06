"""exit_plan ends plan mode. The loop gates it itself: the user approves the plan, which logs
the mode change, or sends feedback, which comes back as the denial reason."""

from pydantic import BaseModel, Field

from cookcode.agent.tools import EXIT_PLAN, Tool, ToolContext, ToolOutput
from cookcode.core.events import ModeChanged


class ExitPlanArgs(BaseModel):
    plan: str = Field(
        min_length=1,
        description=(
            "The plan in Markdown: the files to change, what changes in each, and how the "
            "result will be verified."
        ),
    )


async def _exit(args: ExitPlanArgs, ctx: ToolContext) -> ToolOutput:
    changes = [e.body for e in ctx.history()] if ctx.history is not None else []
    mode = next((b.mode for b in reversed(changes) if isinstance(b, ModeChanged)), None)
    return ToolOutput(
        content=f"The user approved the plan. The mode is now {mode}. Carry out the plan."
    )


def exit_plan_tool() -> Tool[ExitPlanArgs]:
    return Tool(
        name=EXIT_PLAN,
        description=(
            "Leave plan mode by presenting your plan to the user. Call it once you have read "
            "enough to know exactly what to change. The user approves it, and editing tools "
            "unlock, or replies with feedback to revise it."
        ),
        args_model=ExitPlanArgs,
        side_effect="read_only",
        handler=_exit,
        plan_only=True,
    )
