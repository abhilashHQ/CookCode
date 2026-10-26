import asyncio
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_helpers import (
    RigFactory,
    append_tool,
    assert_every_call_resolved,
    assert_no_leaked_tasks,
    bodies,
    results,
)
from harness.agent.approval import NO_APPROVER, Decision, Risk, ScriptedApprover
from harness.agent.gate import Policy, Rule
from harness.agent.repair import NOT_EXECUTED
from harness.agent.tools import Tool, ToolContext, ToolOutput
from harness.core.cancel import CancelToken
from harness.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    Event,
    Interrupted,
    ItemEvent,
    ModelRequestFinished,
    ModelRequestStarted,
    Repaired,
    RunConfig,
    SessionStarted,
    ToolCallStarted,
    UserMessage,
)
from harness.core.items import ToolCall, ToolResult
from harness.core.usage import Usage
from harness.providers.fake import FakeTurn, call, say
from harness.tools.files import edit_tool, read_tool, write_tool


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


class CommandArgs(BaseModel):
    command: str


def fake_bash(ran: list[str]) -> Tool[CommandArgs]:
    async def handler(args: CommandArgs, ctx: ToolContext) -> ToolOutput:
        ran.append(args.command)
        return ToolOutput(content="ok")

    return Tool(
        name="bash",
        description="Record the command without running it.",
        args_model=CommandArgs,
        side_effect="mutating",
        handler=handler,
    )


def trail(events: Sequence[Event], call_id: str) -> list[str]:
    out: list[str] = []
    for event in events:
        body = event.body
        if isinstance(body, ItemEvent):
            if isinstance(body.item, ToolResult) and body.item.call_id == call_id:
                out.append("result")
        elif getattr(body, "call_id", None) == call_id:
            out.append(body.type)
    return out


def result_for(events: Sequence[Event], call_id: str) -> ToolResult:
    return next(result for result in results(events) if result.call_id == call_id)


class BlockingApprover:
    def __init__(self, honor_token: bool) -> None:
        self.honor_token = honor_token
        self.waiting = asyncio.Event()
        self.tokens: list[CancelToken] = []

    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        self.tokens.append(cancel)
        self.waiting.set()
        if self.honor_token:
            await cancel.wait()
        else:
            await asyncio.Event().wait()
        return Decision(action="allow")


class FailingApprover:
    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        raise RuntimeError("nope")


async def test_ask_mode_without_an_approver_denies_mutating_calls_and_runs_reads(
    rigs: RigFactory,
) -> None:
    (rigs.workdir / "b.txt").write_text("hello\n")
    write = call("write", path="a.txt", content="x")
    read = call("read", path="b.txt")
    rig = rigs.make(
        [FakeTurn(items=(write, read)), FakeTurn(items=(say("ok"),))],
        [write_tool(), read_tool()],
        mode="ask",
    )

    outcome = await rig.agent.run("go")

    events = rig.events()
    assert outcome.status == "done"
    assert not (rig.workdir / "a.txt").exists()
    assert result_for(events, write.id) == ToolResult(
        call_id=write.id, status="denied", content=f"denied: {NO_APPROVER}"
    )
    assert trail(events, write.id) == ["approval_requested", "approval_resolved", "result"]
    [resolved] = bodies(events, ApprovalResolved)
    assert (resolved.decision, resolved.reason) == ("deny", NO_APPROVER)
    assert result_for(events, read.id).status == "ok"
    assert trail(events, read.id) == ["tool_call_started", "tool_call_finished", "result"]
    assert_every_call_resolved(events)


async def test_scripted_allow_runs_the_call_after_the_approval_events(rigs: RigFactory) -> None:
    write = call("write", path="a.txt", content="x")
    approver = ScriptedApprover([Decision(action="allow")])
    rig = rigs.make(
        [FakeTurn(items=(write,)), FakeTurn(items=(say("ok"),))],
        [write_tool()],
        mode="ask",
        approver=approver,
    )

    await rig.agent.run("go")

    events = rig.events()
    assert (rig.workdir / "a.txt").read_text() == "x"
    assert trail(events, write.id) == [
        "approval_requested",
        "approval_resolved",
        "tool_call_started",
        "tool_call_finished",
        "result",
    ]
    [(seen_call, risk)] = approver.seen
    assert seen_call == write
    assert (risk.side_effect, risk.rule) == ("mutating", "mode:ask")
    assert bodies(events, ApprovalRequested) == [
        ApprovalRequested(call_id=write.id, risk="mutating", rule="mode:ask")
    ]
    assert_every_call_resolved(events)


async def test_allow_always_covers_later_calls_in_the_response_the_run_and_a_resume(
    rigs: RigFactory,
) -> None:
    ran: list[str] = []
    first = call("bash", command="pytest -q")
    second = call("bash", command="pytest -q -x")
    third = call("bash", command="pytest -q tests")
    approver = ScriptedApprover([Decision(action="allow_always", rule="bash:pytest*")])
    rig = rigs.make(
        [FakeTurn(items=(first, second)), FakeTurn(items=(third,)), FakeTurn(items=(say("ok"),))],
        [fake_bash(ran)],
        mode="ask",
        approver=approver,
    )

    await rig.agent.run("go")

    events = rig.events()
    assert ran == ["pytest -q", "pytest -q -x", "pytest -q tests"]
    assert len(approver.seen) == 1
    assert [b.call_id for b in bodies(events, ApprovalRequested)] == [first.id]
    assert [(r.call_id, r.decision, r.rule) for r in bodies(events, ApprovalResolved)] == [
        (first.id, "allow_always", "bash:pytest*"),
        (second.id, "allow", "bash:pytest*"),
        (third.id, "allow", "bash:pytest*"),
    ]
    assert_every_call_resolved(events)

    rig.log.close()
    again = call("bash", command="pytest -q")
    fresh_approver = ScriptedApprover([])
    fresh = rigs.make(
        [FakeTurn(items=(again,)), FakeTurn(items=(say("ok"),))],
        [fake_bash(ran)],
        log=rigs.open_log(),
        mode="ask",
        approver=fresh_approver,
    )

    await fresh.agent.run("again")

    assert fresh_approver.seen == []
    assert ran[-1] == "pytest -q"
    assert result_for(fresh.events(), again.id).status == "ok"
    assert_every_call_resolved(fresh.events())


async def test_a_deny_rule_wins_in_yolo_without_asking(rigs: RigFactory) -> None:
    ran: list[str] = []
    rm = call("bash", command="rm -rf build")
    approver = ScriptedApprover([])
    rig = rigs.make(
        [FakeTurn(items=(rm,)), FakeTurn(items=(say("ok"),))],
        [fake_bash(ran)],
        mode="yolo",
        policy=Policy(deny=(Rule(tool="bash", pattern="rm *"),)),
        approver=approver,
    )

    await rig.agent.run("go")

    events = rig.events()
    reason = "matches deny rule bash:rm *"
    assert ran == []
    assert approver.seen == []
    assert result_for(events, rm.id) == ToolResult(
        call_id=rm.id, status="denied", content=f"denied: {reason}"
    )
    assert bodies(events, ApprovalResolved) == [
        ApprovalResolved(call_id=rm.id, decision="deny", reason=reason, rule="bash:rm *")
    ]
    assert trail(events, rm.id) == ["approval_resolved", "result"]
    assert_every_call_resolved(events)


async def test_auto_edit_allows_edits_inside_the_workspace_and_asks_outside(
    rigs: RigFactory,
) -> None:
    (rigs.workdir / "a.txt").write_text("old\n")
    (rigs.root / "outside.txt").write_text("old\n")
    inside = call("edit", path="a.txt", old_string="old", new_string="new")
    outside = call("edit", path="../outside.txt", old_string="old", new_string="new")
    approver = ScriptedApprover([Decision(action="deny", reason="stay inside")])
    rig = rigs.make(
        [FakeTurn(items=(inside, outside)), FakeTurn(items=(say("ok"),))],
        [edit_tool()],
        mode="auto-edit",
        approver=approver,
    )

    await rig.agent.run("go")

    events = rig.events()
    assert (rig.workdir / "a.txt").read_text() == "new\n"
    assert (rigs.root / "outside.txt").read_text() == "old\n"
    assert trail(events, inside.id) == [
        "approval_resolved",
        "tool_call_started",
        "tool_call_finished",
        "result",
    ]
    resolved = {r.call_id: r for r in bodies(events, ApprovalResolved)}
    assert (resolved[inside.id].decision, resolved[inside.id].rule) == ("allow", "mode:auto-edit")
    assert [seen for seen, _ in approver.seen] == [outside]
    assert trail(events, outside.id) == ["approval_requested", "approval_resolved", "result"]
    assert result_for(events, outside.id).content == "denied: stay inside"
    assert_every_call_resolved(events)


async def test_yolo_logs_mutating_allows_and_not_read_only_ones(rigs: RigFactory) -> None:
    (rigs.workdir / "b.txt").write_text("hello\n")
    by_mode = call("read", path="b.txt")
    write = call("write", path="a.txt", content="x")
    rig = rigs.make(
        [FakeTurn(items=(by_mode, write)), FakeTurn(items=(say("ok"),))],
        [read_tool(), write_tool()],
        mode="yolo",
    )

    await rig.agent.run("go")

    events = rig.events()
    assert trail(events, by_mode.id) == ["tool_call_started", "tool_call_finished", "result"]
    assert [(r.call_id, r.decision, r.rule) for r in bodies(events, ApprovalResolved)] == [
        (write.id, "allow", "mode:yolo"),
    ]
    assert result_for(events, by_mode.id).status == "ok"
    assert_every_call_resolved(events)


async def test_a_policy_allow_of_a_read_only_call_is_logged(rigs: RigFactory) -> None:
    (rigs.workdir / "b.txt").write_text("hello\n")
    read = call("read", path="b.txt")
    rig = rigs.make(
        [FakeTurn(items=(read,)), FakeTurn(items=(say("ok"),))],
        [read_tool()],
        mode="ask",
        policy=Policy(allow=(Rule(tool="read", pattern="*.txt"),)),
    )

    await rig.agent.run("go")

    events = rig.events()
    assert bodies(events, ApprovalResolved) == [
        ApprovalResolved(
            call_id=read.id, decision="allow", reason=None, rule="read:*.txt"
        )
    ]
    assert trail(events, read.id)[0] == "approval_resolved"
    assert_every_call_resolved(events)


async def test_unknown_tools_and_invalid_arguments_are_never_gated(rigs: RigFactory) -> None:
    unknown = call("nope")
    invalid = call("write", path="a.txt")
    approver = ScriptedApprover([])
    rig = rigs.make(
        [FakeTurn(items=(unknown, invalid)), FakeTurn(items=(say("ok"),))],
        [write_tool()],
        mode="ask",
        approver=approver,
    )

    await rig.agent.run("go")

    events = rig.events()
    assert approver.seen == []
    assert bodies(events, ApprovalRequested) == []
    assert result_for(events, unknown.id).status == "error"
    assert result_for(events, invalid.id).content.startswith("invalid arguments for write")
    assert_every_call_resolved(events)


@pytest.mark.parametrize("honor_token", [True, False])
async def test_cancel_during_an_approval_wait_executes_nothing(
    rigs: RigFactory, honor_token: bool
) -> None:
    ran: list[str] = []
    read = call("read", path="b.txt")
    asked = call("bash", command="make")
    later = call("bash", command="make install")
    approver = BlockingApprover(honor_token)
    rig = rigs.make(
        [FakeTurn(items=(read, asked, later))],
        [read_tool(), fake_bash(ran)],
        mode="ask",
        approver=approver,
    )
    token = CancelToken()

    task = asyncio.create_task(rig.agent.run("go", cancel=token))
    await approver.waiting.wait()
    token.cancel()
    outcome = await task

    events = rig.events()
    assert outcome.status == "interrupted"
    assert ran == []
    assert bodies(events, ToolCallStarted) == []
    assert bodies(events, ApprovalResolved) == []
    assert [b.call_id for b in bodies(events, ApprovalRequested)] == [asked.id]
    assert {r.call_id: (r.status, r.content) for r in results(events)} == {
        c.id: ("interrupted", NOT_EXECUTED) for c in (read, asked, later)
    }
    assert bodies(events, Interrupted) == [Interrupted(phase="tool")]
    assert approver.tokens[0].cancelled
    assert_every_call_resolved(events)
    assert_no_leaked_tasks()


async def test_a_hard_cancel_during_an_approval_wait_propagates(rigs: RigFactory) -> None:
    approver = BlockingApprover(honor_token=False)
    rig = rigs.make(
        [FakeTurn(items=(call("bash", command="make"),))],
        [fake_bash([])],
        mode="ask",
        approver=approver,
    )

    task = asyncio.create_task(rig.agent.run("go"))
    await approver.waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert_no_leaked_tasks()


async def test_an_approver_exception_denies_the_call_and_the_run_continues(
    rigs: RigFactory,
) -> None:
    ran: list[str] = []
    make = call("bash", command="make")
    rig = rigs.make(
        [FakeTurn(items=(make,)), FakeTurn(items=(say("ok"),))],
        [fake_bash(ran)],
        mode="ask",
        approver=FailingApprover(),
    )

    outcome = await rig.agent.run("go")

    events = rig.events()
    reason = "approver failed: RuntimeError: nope"
    assert outcome.status == "done"
    assert len(rig.provider.requests) == 2
    assert ran == []
    assert result_for(events, make.id) == ToolResult(
        call_id=make.id, status="denied", content=f"denied: {reason}"
    )
    [resolved] = bodies(events, ApprovalResolved)
    assert (resolved.decision, resolved.reason) == ("deny", reason)
    assert_every_call_resolved(events)


async def test_resume_after_an_allow_reissues_the_call_once(rigs: RigFactory) -> None:
    dangling = call("append", path="out.txt", line="L1")
    log = rigs.open_log()
    for body in [
        SessionStarted.for_config(
            RunConfig(provider="fake", model="fake-1", harness_version="0.0.1")
        ),
        UserMessage(text="go"),
        ModelRequestStarted(request_id="r1", view="raw", token_estimate=1),
        ItemEvent(item=dangling, request_id="r1"),
        ModelRequestFinished(request_id="r1", stop_reason="tool_use", usage=Usage()),
        ApprovalResolved(call_id=dangling.id, decision="allow", rule="mode:yolo"),
    ]:
        log.append(body)
    log.close()
    retry = call("append", path="out.txt", line="L1")
    rig = rigs.make(
        [FakeTurn(items=(retry,)), FakeTurn(items=(say("done"),))],
        [append_tool()],
        log=rigs.open_log(),
        mode="yolo",
    )

    outcome = await rig.agent.run(None)

    events = rig.events()
    assert outcome.status == "done"
    assert bodies(events, Repaired) == [
        Repaired(call_id=dangling.id, status="interrupted", reason=NOT_EXECUTED)
    ]
    assert (rig.workdir / "out.txt").read_text() == "L1\n"
    assert result_for(events, retry.id).status == "ok"
    assert_every_call_resolved(events)
