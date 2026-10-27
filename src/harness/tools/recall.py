"""Masked tool outputs stay in the log; recall reads one back by the id its placeholder names."""

from pydantic import BaseModel, Field

from harness.agent.tools import Tool, ToolContext, ToolOutput
from harness.core.events import ItemEvent
from harness.core.items import ToolCall, ToolResult


class RecallArgs(BaseModel):
    call_id: str = Field(description="The call id named in a masked output's placeholder.")


async def _recall(args: RecallArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.history is None:
        return ToolOutput(content="recall is unavailable: no session history", status="error")
    internal: str | None = None
    for event in ctx.history():
        body = event.body
        if not isinstance(body, ItemEvent):
            continue
        item = body.item
        if isinstance(item, ToolCall) and args.call_id in (item.provider_call_id, item.id):
            internal = item.id
        elif isinstance(item, ToolResult) and internal is not None and item.call_id == internal:
            content = item.content
            if len(content.encode()) > ctx.max_output_bytes:
                content = content.encode()[: ctx.max_output_bytes].decode("utf-8", "ignore")
                content += "\n[... cut at the output cap]"
            return ToolOutput(content=content)
    return ToolOutput(content=f"no recorded output for call {args.call_id}", status="error")


def recall_tool() -> Tool[RecallArgs]:
    return Tool(
        name="recall",
        description=(
            "Return the full output of an earlier tool call whose output was masked to save "
            "context. Pass the call id shown in the placeholder."
        ),
        args_model=RecallArgs,
        side_effect="read_only",
        handler=_recall,
    )
