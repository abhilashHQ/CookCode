from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from agent_helpers import Rig, RigFactory, bodies, echo_tool, results
from cookcode.agent.context import build_request, items_sha256
from cookcode.agent.hooks import Hook, HookContext
from cookcode.core.events import (
    Event,
    HookBlocked,
    ModelRequestStarted,
    SessionStarted,
    ToolCallStarted,
)
from cookcode.core.features import Features
from cookcode.core.items import Message, ToolCall
from cookcode.core.strategy import Strategy
from cookcode.env.local import LocalEnvironment
from cookcode.providers.fake import FakeTurn, call, say
from cookcode.tools.files import edit_tool, write_tool
from cookcode.tools.verify import registered, repro_tool, verify_first_hook
from tool_helpers import make_kit

CHECK = "grep -q good state.txt"


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    (factory.workdir / "state.txt").write_text("bad\n")
    yield factory
    factory.close_all()


def turn(*calls: ToolCall) -> FakeTurn:
    return FakeTurn(items=calls)


def done() -> FakeTurn:
    return FakeTurn(items=(say("done"),))


def assert_replays(events: Sequence[Event]) -> None:
    for index, event in enumerate(events):
        if isinstance(event.body, ModelRequestStarted):
            rebuilt = build_request(events[:index], model="fake-1", system="", tools=(),
                                    provider="fake", strategy=Strategy())
            assert event.body.items_sha256 == items_sha256(rebuilt.items)


# --- hooks in the loop -----------------------------------------------------------------


async def test_a_tool_hook_blocks_the_call_with_its_message(rigs: RigFactory) -> None:
    async def no_echo(tool_call: ToolCall, ctx: HookContext) -> str | None:
        return "echo is off" if tool_call.name == "echo" else None

    hook = Hook(name="guard", before_tool=no_echo)
    rig = rigs.make([turn(call("echo", text="hi")), done()], [echo_tool()], hooks=[hook])
    assert (await rig.agent.run("go")).status == "done"
    [result] = results(rig.events())
    assert (result.status, result.content) == ("error", "blocked by guard: echo is off")
    [blocked] = bodies(rig.events(), HookBlocked)
    assert (blocked.point, blocked.call_id) == ("tool", result.call_id)
    assert not bodies(rig.events(), ToolCallStarted)


async def test_a_stop_hook_sends_the_model_back_until_it_passes(rigs: RigFactory) -> None:
    answers = iter(["not yet", None])

    async def check(ctx: HookContext) -> str | None:
        return next(answers)

    rig = rigs.make([done(), done()], hooks=[Hook(name="check", before_stop=check)])
    outcome = await rig.agent.run("go")
    assert (outcome.status, outcome.turns) == ("done", 2)
    second = rig.provider.requests[1]
    assert isinstance(second.items[-1], Message) and second.items[-1].text == "[check] not yet"
    [started] = bodies(rig.events(), SessionStarted)
    assert started.config.hooks == ("check",)
    assert_replays(rig.events())


async def test_stop_blocks_are_capped_per_run(rigs: RigFactory) -> None:
    async def never(ctx: HookContext) -> str | None:
        return "never passes"

    rig = rigs.make([done()] * 3, hooks=[Hook(name="never", before_stop=never)],
                    max_stop_blocks=2)
    outcome = await rig.agent.run("go")
    assert (outcome.status, outcome.turns) == ("done", 3)
    assert len(bodies(rig.events(), HookBlocked)) == 2


# --- verify-first ----------------------------------------------------------------------


def verify_rig(rigs: RigFactory, script: list[FakeTurn], max_stop_blocks: int = 3) -> Rig:
    tools = [write_tool(), edit_tool(), repro_tool()]
    return rigs.make(script, tools, hooks=[verify_first_hook()], max_stop_blocks=max_stop_blocks)


async def test_existing_files_are_locked_until_a_reproduction_registers(rigs: RigFactory) -> None:
    script = [
        turn(call("edit", path="state.txt", old_string="bad", new_string="good")),
        turn(call("write", path="test_state.sh", content=CHECK + "\n")),
        turn(call("repro", command="sh test_state.sh")),
        turn(call("edit", path="state.txt", old_string="bad", new_string="good")),
        done(),
    ]
    rig = verify_rig(rigs, script)
    assert (await rig.agent.run("fix it")).status == "done"
    edit_blocked, new_file, repro, edit_ok = results(rig.events())
    assert edit_blocked.status == "error"
    assert "register a failing reproduction" in edit_blocked.content
    assert new_file.status == "ok" and repro.status == "ok"
    assert repro.content.startswith("registered (exit code 1)")
    assert edit_ok.status == "ok" and (rigs.workdir / "state.txt").read_text() == "good\n"
    assert [b.point for b in bodies(rig.events(), HookBlocked)] == ["tool"]
    assert registered(rig.events()) == ("sh test_state.sh", ".")
    assert_replays(rig.events())


async def test_finishing_with_a_failing_reproduction_sends_the_model_back(
    rigs: RigFactory,
) -> None:
    script = [
        turn(call("repro", command=CHECK)),
        done(),
        turn(call("edit", path="state.txt", old_string="bad", new_string="good")),
        done(),
    ]
    rig = verify_rig(rigs, script)
    outcome = await rig.agent.run("fix it")
    assert (outcome.status, outcome.turns) == ("done", 4)
    [blocked] = bodies(rig.events(), HookBlocked)
    assert blocked.point == "stop" and blocked.message.startswith(
        f"the reproduction still fails (exit code 1): {CHECK}"
    )
    assert_replays(rig.events())


async def test_a_passing_command_does_not_register(rigs: RigFactory) -> None:
    rig = verify_rig(rigs, [turn(call("repro", command="true")), done()])
    assert (await rig.agent.run("fix it")).status == "done"
    [result] = results(rig.events())
    assert result.status == "error" and "does not reproduce the bug" in result.content
    assert registered(rig.events()) is None


async def test_changing_files_without_a_reproduction_blocks_the_stop(rigs: RigFactory) -> None:
    script = [turn(call("write", path="new.txt", content="x")), done(), done()]
    rig = verify_rig(rigs, script, max_stop_blocks=1)
    assert (await rig.agent.run("fix it")).status == "done"
    [blocked] = bodies(rig.events(), HookBlocked)
    assert blocked.point == "stop" and "without registering a reproduction" in blocked.message


async def test_a_read_only_turn_finishes_without_a_reproduction(rigs: RigFactory) -> None:
    rig = verify_rig(rigs, [done()])
    outcome = await rig.agent.run("explain")
    assert (outcome.status, outcome.turns) == ("done", 1)


async def test_toolkit_adds_repro_and_the_hook_only_with_the_feature(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    base = await make_kit(Features(), env, tmp_path / "memory")
    assert "repro" not in base.tools.names() and base.hooks == ()
    on = await make_kit(Features(verify_first=True), env, tmp_path / "memory")
    assert "repro" in on.tools.names() and [h.name for h in on.hooks] == ["verify-first"]
