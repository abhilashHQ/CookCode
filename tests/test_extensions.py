from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import RigFactory, bodies, echo_tool, results
from harness.agent.gate import Policy
from harness.agent.loop import Agent, AgentConfig
from harness.agent.tools import ToolRegistry
from harness.core.events import HookBlocked, Limits
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.env.local import LocalEnvironment
from harness.kit import toolkit
from harness.providers.fake import FakeProvider, FakeTurn, call, say
from harness.skills import load_skills, parse_skill, skill_tool, skills_profile
from harness.subagents import Spawner, missing_sections
from harness.userhooks import load_settings, parse_settings, user_hooks
from tool_helpers import make_ctx, run_tool

REPORT = (
    "## Summary\nFound it.\n## Files\n- a.py: read\n## Evidence\n- grep: hit\n"
    "## Open questions\n- none"
)
BLOCK_ECHO = (
    "python3 -c \"import json,sys; d=json.load(sys.stdin); "
    "print('no echo of', d['arguments']['text']); sys.exit(2 if d['tool'] == 'echo' else 0)\""
)


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def settings(*hooks: str) -> str:
    return "".join(f"[[hooks]]\n{hook}\n" for hook in hooks)


# --- user hooks -------------------------------------------------------------------------


async def test_a_pre_tool_hook_blocks_on_exit_2_with_its_output(rigs: RigFactory) -> None:
    found = [("repo", parse_settings(settings(f'point = "pre_tool"\ncommand = """{BLOCK_ECHO}"""'),
                                     "repo"))]
    rig = rigs.make([FakeTurn(items=(call("echo", text="hi"),)), FakeTurn(items=(say("ok"),))],
                    [echo_tool()], hooks=user_hooks(found))
    assert (await rig.agent.run("go")).status == "done"
    [result] = results(rig.events())
    assert result.status == "error" and result.content.endswith("no echo of hi")


async def test_broken_and_unmatched_hooks_let_calls_through(rigs: RigFactory) -> None:
    found = [("repo", parse_settings(settings(
        'point = "pre_tool"\ncommand = "exit 1"',
        'point = "pre_tool"\nmatch = "bash"\ncommand = "exit 2"',
    ), "repo"))]
    rig = rigs.make([FakeTurn(items=(call("echo", text="hi"),)), FakeTurn(items=(say("ok"),))],
                    [echo_tool()], hooks=user_hooks(found))
    await rig.agent.run("go")
    assert [r.status for r in results(rig.events())] == ["ok"]


async def test_a_stop_hook_sees_the_last_message(rigs: RigFactory) -> None:
    command = (
        "python3 -c \"import json,sys; m=json.load(sys.stdin)['last_message']; "
        "print('say DONE'); sys.exit(0 if 'DONE' in m else 2)\""
    )
    found = [("repo", parse_settings(settings(f'point = "stop"\ncommand = """{command}"""'), "r"))]
    rig = rigs.make([FakeTurn(items=(say("finished"),)), FakeTurn(items=(say("DONE"),))],
                    hooks=user_hooks(found))
    outcome = await rig.agent.run("go")
    assert (outcome.status, outcome.turns) == ("done", 2)
    [blocked] = bodies(rig.events(), HookBlocked)
    assert blocked.message == "say DONE"


async def test_settings_load_user_then_repo_and_reject_junk(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".harness").mkdir(parents=True)
    (tmp_path / "repo" / ".harness" / "settings.toml").write_text(
        settings('point = "stop"\ncommand = "true"'))
    (tmp_path / "user.toml").write_text(settings('point = "pre_tool"\ncommand = "true"'))
    found = await load_settings(LocalEnvironment(tmp_path / "repo"), tmp_path / "user.toml")
    assert [(source.endswith(name), s.hooks[0].point) for (source, s), name in
            zip(found, ["user.toml", "settings.toml"], strict=True)] == [
        (True, "pre_tool"), (True, "stop")]
    with pytest.raises(ValueError, match="bad settings"):
        parse_settings(settings('point = "later"\ncommand = "x"'), "x")
    with pytest.raises(ValueError, match="not a valid regex"):
        parse_settings(settings('point = "pre_tool"\nmatch = "("\ncommand = "x"'), "x")


# --- skills -----------------------------------------------------------------------------


def write_skill(root: Path, name: str, description: str, body: str = "Do it well.") -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")


def test_parse_skill_needs_a_name_and_a_description() -> None:
    skill = parse_skill("---\nname: pdf\ndescription: 'Read PDFs'\n---\nUse pdftotext.\n", "s")
    assert skill is not None and (skill.name, skill.description, skill.body) == (
        "pdf", "Read PDFs", "Use pdftotext.")
    assert parse_skill("no frontmatter", "s") is None
    assert parse_skill("---\nname: Bad Name\ndescription: x\n---\n", "s") is None
    assert parse_skill("---\nname: ok\n---\n", "s") is None


async def test_repo_skills_win_and_the_tool_loads_bodies(tmp_path: Path) -> None:
    repo, user = tmp_path / "repo", tmp_path / "user"
    write_skill(repo / ".harness" / "skills", "release", "Cut a release", "Tag, then push.")
    (repo / ".harness" / "skills" / "release" / "checklist.md").write_text("- tag\n")
    write_skill(user, "release", "old user version")
    write_skill(user, "review", "Review a diff")
    skills = await load_skills(LocalEnvironment(repo), user)
    assert [(s.name, s.description) for s in skills] == [
        ("release", "Cut a release"), ("review", "Review a diff")]
    assert skills_profile(skills) == (
        "Skills you can load with the skill tool:\n"
        "- release: Cut a release\n- review: Review a diff"
    )
    tool = skill_tool(skills)
    out = await run_tool(tool, make_ctx(repo), name="release")
    assert out.content.startswith("Tag, then push.")
    assert out.content.endswith(".harness/skills/release/checklist.md")
    missing = await run_tool(tool, make_ctx(repo), name="nope")
    assert missing.status == "error" and "release, review" in missing.content


async def test_skills_enter_the_kit_only_with_the_feature(tmp_path: Path) -> None:
    write_skill(tmp_path / "home" / "skills", "review", "Review a diff")
    (tmp_path / "repo").mkdir()
    env = LocalEnvironment(tmp_path / "repo")
    off = await toolkit(Strategy(), Features(), env, home=tmp_path / "home",
                        memory_root=tmp_path / "m")
    on = await toolkit(Strategy(), Features(skills=True), env, home=tmp_path / "home",
                       memory_root=tmp_path / "m")
    assert "skill" not in off.tools.names() and off.profile == ""
    assert "skill" in on.tools.names() and on.system("base").endswith("- review: Review a diff")


# --- sub-agents -------------------------------------------------------------------------


def test_missing_sections() -> None:
    assert missing_sections(REPORT) == []
    assert missing_sections("## Summary\nx") == ["Files", "Evidence", "Open questions"]


async def test_a_sub_agent_reports_back_after_the_contract_is_enforced(rigs: RigFactory) -> None:
    script = [
        FakeTurn(items=(call("task", prompt="find the bug"),)),
        FakeTurn(items=(call("echo", text="looking"),)),
        FakeTurn(items=(say("It is in a.py."),)),
        FakeTurn(items=(say(REPORT),)),
        FakeTurn(items=(say("thanks"),)),
    ]
    provider = FakeProvider(script)
    env = LocalEnvironment(rigs.workdir)
    spawner = Spawner(provider=provider, store=rigs.store, model="fake-1",
                      system_prompt="be brief", mode="yolo", policy=Policy())
    kit = await toolkit(Strategy(), Features(subagents=True), env, home=rigs.root / "home",
                        memory_root=rigs.root / "m", spawner=spawner)
    tools = ToolRegistry([*kit.tools, echo_tool()])
    log = rigs.open_log()
    agent = Agent(log=log, provider=provider, env=env, tools=tools, hooks=kit.hooks,
                  config=AgentConfig(model="fake-1", system_prompt="be brief", mode="yolo",
                                     limits=Limits(max_turns=10)))
    assert (await agent.run("go")).status == "done"
    [result] = results(log.read())
    assert result.status == "ok" and result.content.startswith(REPORT)
    assert "sub-agent session" in result.content and "done, 3 turns" in result.content
    child_request = provider.requests[1]
    assert "You are a sub-agent" in child_request.system
    assert "task" not in {tool.name for tool in child_request.tools}
    assert "[report] your final message lacks the report sections" in (
        provider.requests[3].items[-1].model_dump_json())
    assert len(rigs.store.list_sessions()) == 2


async def test_subagents_need_a_spawner(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs a spawner"):
        await toolkit(Strategy(), Features(subagents=True), LocalEnvironment(tmp_path),
                      home=tmp_path, memory_root=tmp_path)
