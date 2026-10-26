from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import RigFactory, assert_every_call_resolved, bodies, results
from harness.agent.approval import Decision, ScriptedApprover
from harness.agent.gate import Policy, Rule
from harness.agent.undo import undo
from harness.core.events import ApprovalResolved, CheckpointCreated, ToolCallFinished
from harness.env.local import LocalEnvironment
from harness.providers.fake import FakeTurn, call, say
from harness.store.checkpoints import FileCheckpointStore
from harness.tools.bash import bash_tool
from harness.tools.files import edit_tool, read_tool, write_tool


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


async def test_leaving_the_workspace_and_returning_keeps_relative_deny_rules(
    rigs: RigFactory, tmp_path: Path
) -> None:
    root = rigs.workdir.resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "secrets").mkdir()
    (root / "secrets" / "key").write_text("SECRET")
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command=f"cd {outside}"),)),
            FakeTurn(items=(call("bash", command=f"cd {root}"),)),
            FakeTurn(
                items=(call("edit", path="secrets/key", old_string="SECRET", new_string="X"),)
            ),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool(), edit_tool()],
        mode="yolo",
        policy=Policy(deny=(Rule(tool="edit", pattern="secrets/*"),)),
    )

    await rig.agent.run("go")

    events = rig.events()
    assert [f.cwd for f in bodies(events, ToolCallFinished)] == [str(outside.resolve()), "."]
    last = bodies(events, ApprovalResolved)[-1]
    assert (last.decision, last.rule) == ("deny", "edit:secrets/*")
    assert (root / "secrets" / "key").read_text() == "SECRET"
    assert_every_call_resolved(events)


async def test_auto_edit_allows_edits_after_returning_to_the_workspace(
    rigs: RigFactory, tmp_path: Path
) -> None:
    root = rigs.workdir.resolve()
    (root / "a.py").write_text("v0\n")
    approver = ScriptedApprover([Decision(action="allow"), Decision(action="allow")])
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command=f"cd {tmp_path}"),)),
            FakeTurn(items=(call("bash", command=f"cd {root}"),)),
            FakeTurn(items=(call("edit", path="a.py", old_string="v0", new_string="v1"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool(), edit_tool()],
        mode="auto-edit",
        approver=approver,
    )

    await rig.agent.run("go")

    assert len(approver.seen) == 2
    last = bodies(rig.events(), ApprovalResolved)[-1]
    assert (last.decision, last.rule) == ("allow", "mode:auto-edit")
    assert (root / "a.py").read_text() == "v1\n"


@pytest.mark.parametrize("spelling", ["absolute", "via-parent"])
async def test_another_spelling_of_a_denied_path_is_denied(
    rigs: RigFactory, spelling: str
) -> None:
    root = rigs.workdir.resolve()
    (root / "secrets").mkdir()
    (root / "secrets" / "key").write_text("SECRET")
    if spelling == "absolute":
        path = str(root / "secrets" / "key")
    else:
        path = f"../{root.name}/secrets/key"
    rig = rigs.make(
        [
            FakeTurn(items=(call("read", path=path),)),
            FakeTurn(items=(call("write", path=path, content="X"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [read_tool(), write_tool()],
        mode="yolo",
        policy=Policy(
            deny=(Rule(tool="read", pattern="secrets/*"), Rule(tool="write", pattern="secrets/*"))
        ),
    )

    await rig.agent.run("go")

    assert [r.status for r in results(rig.events())] == ["denied", "denied"]
    assert (root / "secrets" / "key").read_text() == "SECRET"


async def test_undo_with_two_spellings_of_one_file_restores_the_original(
    rigs: RigFactory, tmp_path: Path
) -> None:
    root = rigs.workdir.resolve()
    (root / "a.py").write_text("v0\n")
    store = FileCheckpointStore(tmp_path / "cps")
    rig = rigs.make(
        [
            FakeTurn(
                items=(call("edit", path=str(root / "a.py"), old_string="v0", new_string="v1"),)
            ),
            FakeTurn(items=(call("edit", path="a.py", old_string="v1", new_string="v2"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [edit_tool()],
        mode="yolo",
        checkpoints=store,
    )
    await rig.agent.run("turn 1")
    assert [c.paths for c in bodies(rig.events(), CheckpointCreated)] == [("a.py",), ("a.py",)]

    restored = await undo(log=rig.log, env=LocalEnvironment(root), checkpoints=store)

    assert restored is not None and restored.paths == ("a.py",)
    assert (root / "a.py").read_text() == "v0\n"


async def test_an_allow_always_rule_does_not_cover_an_assignment_prefixed_command(
    rigs: RigFactory,
) -> None:
    (rigs.workdir / "victim").mkdir()
    approver = ScriptedApprover(
        [
            Decision(action="allow_always", rule="bash:echo*"),
            Decision(action="deny", reason="no"),
        ]
    )
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command="echo ok"),)),
            FakeTurn(items=(call("bash", command="GREETING=hi echo ok"),)),
            FakeTurn(items=(call("bash", command="echo=1 rm -rf victim"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool()],
        mode="ask",
        approver=approver,
    )

    await rig.agent.run("go")

    resolved = [(r.decision, r.rule) for r in bodies(rig.events(), ApprovalResolved)]
    assert resolved == [
        ("allow_always", "bash:echo*"),
        ("allow", "bash:echo*"),
        ("deny", None),
    ]
    assert (rigs.workdir / "victim").exists()


async def test_the_approver_sees_the_cwd_and_the_canonical_subjects(
    rigs: RigFactory, tmp_path: Path
) -> None:
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    approver = ScriptedApprover([Decision(action="allow"), Decision(action="deny")])
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command=f"cd {outside}"),)),
            FakeTurn(items=(call("write", path="target.txt", content="clobbered"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool(), write_tool()],
        mode="ask",
        approver=approver,
    )

    await rig.agent.run("go")

    (_, first), (_, second) = approver.seen
    assert (first.cwd, first.subjects) == (".", (f"cd {outside}",))
    assert (second.cwd, second.subjects) == (str(outside), (str(outside / "target.txt"),))
    assert not (outside / "target.txt").exists()


async def test_an_allow_always_rule_for_another_tool_is_logged_as_a_plain_allow(
    rigs: RigFactory,
) -> None:
    (rigs.workdir / "a.py").write_text("v0")
    (rigs.workdir / "victim").mkdir()
    approver = ScriptedApprover(
        [Decision(action="allow_always", rule="bash:*"), Decision(action="deny")]
    )
    rig = rigs.make(
        [
            FakeTurn(items=(call("edit", path="a.py", old_string="v0", new_string="v1"),)),
            FakeTurn(items=(call("bash", command="rm -rf victim"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool(), edit_tool()],
        mode="ask",
        approver=approver,
    )

    await rig.agent.run("go")

    first, second = bodies(rig.events(), ApprovalResolved)
    assert (first.decision, first.rule) == ("allow", None)
    assert first.reason == "allow_always rule bash:* does not cover this call; allowed once"
    assert second.decision == "deny"
    assert len(approver.seen) == 2
    assert (rig.workdir / "a.py").read_text() == "v1"
    assert (rigs.workdir / "victim").exists()


@pytest.mark.parametrize("rule", ["edit:b.py", "garbage", "edit:"])
async def test_an_allow_always_rule_that_misses_the_call_is_not_remembered(
    rigs: RigFactory, rule: str
) -> None:
    (rigs.workdir / "a.py").write_text("v0")
    approver = ScriptedApprover(
        [Decision(action="allow_always", rule=rule), Decision(action="deny")]
    )
    rig = rigs.make(
        [
            FakeTurn(items=(call("edit", path="a.py", old_string="v0", new_string="v1"),)),
            FakeTurn(items=(call("edit", path="a.py", old_string="v1", new_string="v2"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [edit_tool()],
        mode="ask",
        approver=approver,
    )

    await rig.agent.run("go")

    first, second = bodies(rig.events(), ApprovalResolved)
    assert (first.decision, first.rule) == ("allow", None)
    assert first.reason == f"allow_always rule {rule} does not cover this call; allowed once"
    assert second.decision == "deny"
