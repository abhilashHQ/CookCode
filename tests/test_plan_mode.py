from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_helpers import RigFactory, assert_every_call_resolved, bodies, results
from cookcode.agent.approval import Decision, ScriptedApprover
from cookcode.agent.context import PLAN_NOTE, build_request
from cookcode.agent.gate import PLAN_RULE, GateDecision, Policy, Rule, decide, mode_at
from cookcode.agent.tools import EXIT_PLAN, ToolRegistry
from cookcode.core.events import ApprovalResolved, ModeChanged
from cookcode.core.items import Message
from cookcode.providers.fake import FakeTurn, call, say
from cookcode.tools import default_tools
from cookcode.tools.files import edit_tool, read_tool, write_tool
from cookcode.tools.plan import exit_plan_tool

TOOLS = {tool.name: tool for tool in default_tools()}


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def plan_gate(name: str, deny: tuple[str, ...] = (), **arguments: Any) -> GateDecision:
    rules = tuple(Rule(tool=s.partition(":")[0], pattern=s.partition(":")[2]) for s in deny)
    return decide(
        tool=TOOLS[name],
        arguments=arguments,
        cwd=".",
        root="/ws",
        mode="plan",
        policy=Policy(deny=rules),
        session_allow=(),
    )


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat a.py | grep foo | head -5",
        "git status && git log --oneline -5",
        "git -C sub --no-pager diff HEAD~1",
        "find . -name '*.py' -type f",
        "rg -n TODO src 2>/dev/null",
        "sort -u names.txt",
        "cd src && ls",
        "diff a.txt b.txt",
    ],
)
def test_plan_mode_allows_read_only_commands(command: str) -> None:
    decision = plan_gate("bash", command=command)
    assert decision.action == "allow"
    assert decision.rule == PLAN_RULE


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("rm -rf build", "rm is not on the read-only list"),
        ("echo hi > out.txt", "redirects output to a file"),
        ("cat a | tee b", "tee is not on the read-only list"),
        ("find . -name x -delete", "find -delete"),
        ("find . -exec rm {} ;", "find -exec"),
        ("sort -uo out.txt in.txt", "sort -uo"),
        ("git commit -m x", "git commit is not on the read-only list"),
        ("git diff --output=patch.txt", "git diff --output=patch.txt"),
        ("git -c core.pager=sh log", "git -c is not on the read-only list"),
        ("rg --pre ./script foo", "rg --pre"),
        ("ls $(rm -rf x)", "it uses substitution"),
        ("diff <(rm -rf x) b", "it uses substitution"),
        ("bash -c 'ls'", "bash is not on the read-only list"),
        ("timeout 5 ls", "timeout is not on the read-only list"),
        ("LD_PRELOAD=x.so ls", "it runs code the gate cannot inspect"),
        ("python -c 'print(1)'", "python is not on the read-only list"),
    ],
)
def test_plan_mode_denies_commands_that_can_change_state(command: str, why: str) -> None:
    decision = plan_gate("bash", command=command)
    assert decision.action == "deny"
    assert decision.rule == PLAN_RULE
    assert why in decision.reason
    assert "exit_plan" in decision.reason


def test_plan_mode_denies_mutating_tools_even_inside_the_workspace() -> None:
    decision = plan_gate("write", path="a.txt", content="x")
    assert decision.action == "deny"
    assert "write is a mutating tool" in decision.reason


def test_plan_mode_still_applies_deny_rules_first() -> None:
    assert plan_gate("bash", deny=("bash:cat *",), command="cat secrets").rule == "bash:cat *"
    assert plan_gate("read", deny=("read:secrets",), path="secrets").action == "deny"


def test_plan_mode_allows_read_only_tools() -> None:
    assert plan_gate("read", path="a.txt").action == "allow"


def test_exit_plan_schema_is_sent_only_in_plan_mode() -> None:
    registry = ToolRegistry([read_tool(), exit_plan_tool()])
    assert [s.name for s in registry.schemas()] == ["read"]
    assert [s.name for s in registry.schemas(plan=True)] == ["read", EXIT_PLAN]


async def test_plan_approved_into_ask_unlocks_edits(rigs: RigFactory) -> None:
    (rigs.workdir / "a.txt").write_text("old\n")
    early = call("edit", path="a.txt", old_string="old", new_string="new")
    plan = call(EXIT_PLAN, plan="1. change old to new in a.txt")
    late = call("edit", path="a.txt", old_string="old", new_string="new")
    approver = ScriptedApprover([Decision(action="allow"), Decision(action="allow")])
    rig = rigs.make(
        [
            FakeTurn(items=(early,)),
            FakeTurn(items=(plan,)),
            FakeTurn(items=(late,)),
            FakeTurn(items=(say("done"),)),
        ],
        [read_tool(), edit_tool(), exit_plan_tool()],
        mode="plan",
        approver=approver,
    )

    outcome = await rig.agent.run("change it")

    events = rig.events()
    assert outcome.status == "done"
    assert_every_call_resolved(events)
    by_call = {r.call_id: r for r in results(events)}
    assert by_call[early.id].status == "denied"
    assert "exit_plan" in by_call[early.id].content
    assert by_call[plan.id].status == "ok"
    assert "The mode is now ask" in by_call[plan.id].content
    assert by_call[late.id].status == "ok"
    assert (rigs.workdir / "a.txt").read_text() == "new\n"
    assert [(c.mode, c.source) for c in bodies(events, ModeChanged)] == [
        ("plan", "run"),
        ("ask", "exit_plan"),
    ]
    assert rig.agent.mode == "ask"
    assert mode_at(events) == "ask"
    assert [call.name for call, _ in approver.seen] == [EXIT_PLAN, "edit"]


async def test_the_model_sees_the_plan_note_and_exit_plan_only_while_planning(
    rigs: RigFactory,
) -> None:
    plan = call(EXIT_PLAN, plan="do it")
    rig = rigs.make(
        [FakeTurn(items=(plan,)), FakeTurn(items=(say("ok"),))],
        [read_tool(), exit_plan_tool()],
        mode="plan",
        approver=ScriptedApprover([Decision(action="allow_always")]),
    )

    await rig.agent.run("go")

    first, second = rig.provider.requests
    assert [s.name for s in first.tools] == ["read", EXIT_PLAN]
    assert [s.name for s in second.tools] == ["read"]
    notes = [i.text for i in first.items if isinstance(i, Message) and i.role == "user"]
    assert notes == [PLAN_NOTE, "go"]
    assert rig.agent.mode == "auto-edit"


async def test_rejected_plan_returns_feedback_and_stays_in_plan_mode(rigs: RigFactory) -> None:
    plan = call(EXIT_PLAN, plan="rewrite everything")
    rig = rigs.make(
        [FakeTurn(items=(plan,)), FakeTurn(items=(say("revising"),))],
        [exit_plan_tool()],
        mode="plan",
        approver=ScriptedApprover([Decision(action="deny", reason="too broad, touch one file")]),
    )

    await rig.agent.run("go")

    events = rig.events()
    result = next(r for r in results(events) if r.call_id == plan.id)
    assert result.status == "denied"
    assert "too broad, touch one file" in result.content
    assert rig.agent.mode == "plan"
    assert [c.source for c in bodies(events, ModeChanged)] == ["run"]
    assert bodies(events, ApprovalResolved)[-1].rule == PLAN_RULE


async def test_exit_plan_outside_plan_mode_is_an_error(rigs: RigFactory) -> None:
    plan = call(EXIT_PLAN, plan="x")
    rig = rigs.make(
        [FakeTurn(items=(plan,)), FakeTurn(items=(say("ok"),))],
        [exit_plan_tool()],
        mode="ask",
    )

    await rig.agent.run("go")

    result = next(r for r in results(rig.events()) if r.call_id == plan.id)
    assert result.status == "error"
    assert "not in plan mode" in result.content


async def test_leaving_plan_mode_between_runs_tells_the_model(rigs: RigFactory) -> None:
    log = rigs.open_log()
    rig = rigs.make(
        [FakeTurn(items=(say("planned"),)), FakeTurn(items=(say("ok"),))],
        [write_tool()],
        log=log,
        mode="plan",
    )
    await rig.agent.run("plan it")
    rig.agent.set_mode("auto-edit")

    await rig.agent.run("now do it")

    changes = [(c.mode, c.previous) for c in bodies(rig.events(), ModeChanged)]
    assert changes == [("plan", None), ("auto-edit", "plan")]
    request = build_request(
        rig.events(), model="fake-1", system="", tools=(), provider=None, strategy=None
    )
    texts = [i.text for i in request.items if isinstance(i, Message) and i.role == "user"]
    assert texts[-2:] == ["[mode] Plan mode is off. The mode is now auto-edit.", "now do it"]
