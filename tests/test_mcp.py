import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import RigFactory, results
from harness.agent.tools import loaded_tools
from harness.config import Mcp, McpServer, Settings
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.env.local import LocalEnvironment
from harness.kit import toolkit
from harness.mcp import McpHub, tool_search
from harness.providers.fake import FakeTurn, call, say

SERVER = McpServer(command=sys.executable, args=(str(Path(__file__).parent / "mcp_server.py"),))
ADD = "mcp__calc__add"


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


async def test_a_hub_lists_and_calls_server_tools(tmp_path: Path) -> None:
    hub = McpHub()
    try:
        await hub.start({"calc": SERVER, "ghost": McpServer(command="/nonexistent")}, str(tmp_path))
        tools = {tool.name: tool for tool in hub.tools()}
        assert set(tools) == {ADD, "mcp__calc__explode"}
        assert all(t.deferred and t.side_effect == "external" for t in tools.values())
        schema = tools[ADD].schema().parameters
        assert set(schema["properties"]) == {"a", "b"}
        assert await hub.call("calc", "add", {"a": 2, "b": 3}, 10) == ("5", False)
        text, is_error = await hub.call("calc", "explode", {}, 10)
        assert is_error and "explode" in text
        profile = hub.profile()
        assert f"- {ADD}: Add two integers." in profile and "- ghost:" in profile
    finally:
        await hub.aclose()
    assert hub.servers == {}


async def test_deferred_schemas_reach_the_model_only_after_tool_search(
    rigs: RigFactory, tmp_path: Path
) -> None:
    settings = Settings(mcp=Mcp(servers={"calc": SERVER}))
    kit = await toolkit(Strategy(), Features(mcp=True), LocalEnvironment(rigs.workdir),
                        home=tmp_path / "home", memory_root=tmp_path / "m", settings=settings)
    try:
        assert "tool_search(names=[...])" in kit.profile
        script = [
            FakeTurn(items=(call("tool_search", names=[ADD, "nope"]),)),
            FakeTurn(items=(call(ADD, a=20, b=22),)),
            FakeTurn(items=(say("42"),)),
        ]
        rig = rigs.make(script, list(kit.tools), hooks=kit.hooks)
        assert (await rig.agent.run("add")).status == "done"
        first, second, _ = (
            {tool.name for tool in request.tools} for request in rig.provider.requests
        )
        assert ADD not in first and "tool_search" in first
        assert ADD in second and "mcp__calc__explode" not in second
        search, added = results(rig.events())
        assert search.content.startswith(f"loaded {ADD}") and "Unknown: nope" in search.content
        assert added.content == "42"
        assert loaded_tools(rig.events()) == frozenset({ADD, "nope"})
    finally:
        await kit.aclose()


async def test_mcp_needs_the_feature(tmp_path: Path) -> None:
    settings = Settings(mcp=Mcp(servers={"calc": SERVER}))
    kit = await toolkit(Strategy(), Features(), LocalEnvironment(tmp_path), home=tmp_path,
                        memory_root=tmp_path, settings=settings)
    assert "tool_search" not in kit.tools.names() and kit.hub.servers == {}


async def test_a_failed_search_loads_nothing(rigs: RigFactory) -> None:
    search = tool_search([])
    rig = rigs.make([FakeTurn(items=(call("tool_search", names=["x"]),)),
                     FakeTurn(items=(say("ok"),))], [search])
    await rig.agent.run("go")
    assert results(rig.events())[0].status == "error"
    assert loaded_tools(rig.events()) == frozenset()
