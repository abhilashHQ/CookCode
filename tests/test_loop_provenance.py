from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_helpers import RigFactory, bodies, echo_tool, kinds
from harness.agent.loop import Agent, AgentConfig
from harness.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from harness.core.events import (
    ItemEvent,
    ModelRequestFinished,
    ModelRequestStarted,
    RunConfig,
    RunStarted,
    SessionStarted,
    ToolCallStarted,
    UserMessage,
    compute_config_id,
)
from harness.core.usage import Usage
from harness.env.local import LocalEnvironment
from harness.providers.fake import FakeProvider, FakeTurn, call, say


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


class CdArgs(BaseModel):
    to: str | None = None


def cd_tool(seen: list[str]) -> Tool[CdArgs]:
    async def handler(args: CdArgs, ctx: ToolContext) -> ToolOutput:
        seen.append(ctx.cwd)
        return ToolOutput(content=ctx.cwd, cwd=args.to)

    return Tool(
        name="cd",
        description="Record the cwd and optionally report a new one.",
        args_model=CdArgs,
        side_effect="mutating",
        handler=handler,
    )


async def test_new_session_logs_run_started_after_session_started(rigs: RigFactory) -> None:
    rig = rigs.make([FakeTurn(items=(say("ok"),))])
    await rig.agent.run("hi")
    assert kinds(rig.events())[:4] == [
        "session_started",
        "run_started",
        "user_message",
        "model_request_started",
    ]
    [session] = bodies(rig.events(), SessionStarted)
    [run] = bodies(rig.events(), RunStarted)
    assert run.config == session.config
    assert run.config_id == session.config_id == compute_config_id(run.config)


async def test_second_run_with_another_model_logs_a_different_config_id(
    rigs: RigFactory,
) -> None:
    first = rigs.make([FakeTurn(items=(say("one"),))])
    await first.agent.run("hi")
    second = Agent(
        log=first.log,
        provider=FakeProvider([FakeTurn(items=(say("two"),))]),
        env=LocalEnvironment(rigs.workdir),
        tools=ToolRegistry([]),
        config=AgentConfig(model="fake-2", system_prompt="be brief"),
    )
    await second.run("again")
    runs = bodies(first.events(), RunStarted)
    assert len(runs) == 2
    assert runs[0].config.model == "fake-1"
    assert runs[1].config.model == "fake-2"
    assert runs[0].config_id != runs[1].config_id
    assert kinds(first.events()).count("session_started") == 1


async def test_a_resumed_run_logs_run_started_before_repair(rigs: RigFactory) -> None:
    dangling = call("echo", text="x")
    log = rigs.open_log()
    for body in [
        SessionStarted.for_config(
            RunConfig(provider="fake", model="fake-1", harness_version="0.0.1")
        ),
        UserMessage(text="go"),
        ModelRequestStarted(request_id="r1", view="raw", token_estimate=1),
        ItemEvent(item=dangling, request_id="r1"),
        ModelRequestFinished(request_id="r1", stop_reason="tool_use", usage=Usage()),
        ToolCallStarted(
            call_id=dangling.id, tool="echo", arguments=dangling.arguments, side_effect="mutating"
        ),
    ]:
        log.append(body)
    prefix = len(log.read())
    rig = rigs.make([FakeTurn(items=(say("ok"),))], [echo_tool()], log=log)
    await rig.agent.run(None)
    assert kinds(rig.events()[prefix:])[:2] == ["run_started", "repaired"]
    assert kinds(rig.events()).count("session_started") == 1


async def test_cwd_reported_by_a_tool_reaches_the_next_call(rigs: RigFactory) -> None:
    seen: list[str] = []
    rig = rigs.make(
        [
            FakeTurn(items=(call("cd", to="sub"),)),
            FakeTurn(items=(call("cd"),)),
            FakeTurn(items=(say("ok"),)),
        ],
        [cd_tool(seen)],
    )
    await rig.agent.run("go")
    assert seen == [".", "sub"]


async def test_cwd_is_a_projection_of_the_log(rigs: RigFactory) -> None:
    seen: list[str] = []
    first = rigs.make(
        [FakeTurn(items=(call("cd", to="sub"),)), FakeTurn(items=(say("ok"),))], [cd_tool(seen)]
    )
    await first.agent.run("go")
    first.log.close()
    second = rigs.make(
        [FakeTurn(items=(call("cd"),)), FakeTurn(items=(say("ok"),))],
        [cd_tool(seen)],
        log=rigs.open_log(),
    )
    await second.agent.run("again")
    assert seen == [".", "sub"]


async def test_a_tool_reporting_no_cwd_leaves_it_unchanged(rigs: RigFactory) -> None:
    seen: list[str] = []
    rig = rigs.make(
        [
            FakeTurn(items=(call("cd", to="sub"),)),
            FakeTurn(items=(call("cd"),)),
            FakeTurn(items=(call("cd"),)),
            FakeTurn(items=(say("ok"),)),
        ],
        [cd_tool(seen)],
    )
    await rig.agent.run("go")
    assert seen == [".", "sub", "sub"]


async def test_the_configured_cwd_is_the_starting_point(rigs: RigFactory) -> None:
    seen: list[str] = []
    rig = rigs.make(
        [FakeTurn(items=(call("cd"),)), FakeTurn(items=(say("ok"),))], [cd_tool(seen)], cwd="start"
    )
    await rig.agent.run("go")
    assert seen == ["start"]
