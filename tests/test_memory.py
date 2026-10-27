import hashlib
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import Rig, RigFactory
from harness.agent.context import MEMORY_VIEW, build_request, items_sha256
from harness.core.events import Event, ModelRequestStarted
from harness.core.features import Features
from harness.core.strategy import Strategy
from harness.env.local import LocalEnvironment
from harness.kit import Toolkit
from harness.memory.layer import fresh, memory_parts, parse_facts, repo_key
from harness.memory.store import FactStore
from harness.providers.fake import FakeTurn, call, say
from tool_helpers import make_ctx, make_kit, run_tool

FACT = '{"fact": "Tests run with sh check.sh.", "paths": ["check.sh"], "updates": null}'


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    (factory.workdir / "check.sh").write_text("exit 0\n")
    yield factory
    factory.close_all()


def git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "a.txt").write_text("a\n")
    env = {"GIT_AUTHOR_DATE": "@1767225600 +0000", "GIT_COMMITTER_DATE": "@1767225600 +0000",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", *args], cwd=path, env=env, check=True)


# --- store ------------------------------------------------------------------------------


def test_updates_retire_the_old_fact_and_search_sees_only_latest(tmp_path: Path) -> None:
    with FactStore(tmp_path / "facts.db") as store:
        old = store.add("Build with make all.", {"Makefile": "h1"}, "c1")
        store.add("Tests live in tests/ and use pytest.", {"tests/x.py": "h2"}, "c1")
        new = store.add("Build with make release.", {"Makefile": "h3"}, "c2", "updates", old.id)
        assert store.latest()[0].id == new.id
        assert old.id not in {f.id for f in store.latest()}
        assert [f.id for f in store.search("how do I build? make")] == [new.id]
        assert store.search("??") == []
        retired = store.get(old.id)
        assert retired is not None and not retired.latest


async def test_a_fact_goes_stale_when_a_cited_file_changes(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    (tmp_path / "check.sh").write_text("exit 0\n")
    digest = hashlib.sha256(b"exit 0\n").hexdigest()
    with FactStore(tmp_path / "facts.db") as store:
        fact = store.add("t", {"check.sh": digest}, "")
        assert await fresh(env, [fact]) == ([fact], 0)
        (tmp_path / "check.sh").write_text("exit 1\n")
        assert await fresh(env, [fact]) == ([], 1)
        (tmp_path / "check.sh").unlink()
        assert await fresh(env, [fact]) == ([], 1)


def test_parse_facts_skips_lines_that_are_not_facts() -> None:
    junk = ['{"fact": "", "paths": []}', "[1, 2]", '{"fact": "x", "paths": "a"}']
    reply = "\n".join(["Here you go:", FACT, *junk, '{"fact": "y", "paths": [], "updates": "f1"}'])
    assert parse_facts(reply) == [("Tests run with sh check.sh.", ["check.sh"], None),
                                  ("y", [], "f1")]


async def test_copies_of_a_repository_share_a_key(tmp_path: Path) -> None:
    git_repo(tmp_path / "one")
    git_repo(tmp_path / "two")
    (tmp_path / "plain").mkdir()
    one = await repo_key(LocalEnvironment(tmp_path / "one"))
    assert one == await repo_key(LocalEnvironment(tmp_path / "two"))
    assert one != await repo_key(LocalEnvironment(tmp_path / "plain"))


# --- in the loop ------------------------------------------------------------------------


async def facts_rig(rigs: RigFactory, script: list[FakeTurn], memory: Path) -> tuple[Rig, Toolkit]:
    kit = await make_kit(Features(facts=True), LocalEnvironment(rigs.workdir), memory)
    return rigs.make(script, list(kit.tools), hooks=kit.hooks), kit


def side_views(events: list[Event]) -> list[str]:
    return [e.body.view for e in events if isinstance(e.body, ModelRequestStarted)]


async def test_a_finished_run_writes_facts_the_next_session_sees(
    rigs: RigFactory, tmp_path: Path
) -> None:
    memory = tmp_path / "memory"
    rig, kit = await facts_rig(rigs, [FakeTurn(items=(say("done"),)), FakeTurn(items=(say(FACT),))],
                               memory)
    assert kit.profile == ""
    outcome = await rig.agent.run("look around")
    assert (outcome.status, outcome.turns) == ("done", 1)
    events = rig.events()
    assert side_views(events) == ["raw", MEMORY_VIEW]
    memory_request = rig.provider.requests[1]
    assert memory_request.tools == ()
    assert "[user] look around" in memory_request.items[0].model_dump_json()
    for index, event in enumerate(events):
        if isinstance(event.body, ModelRequestStarted) and event.body.view == "raw":
            rebuilt = build_request(events[:index], model="fake-1", system="", tools=(),
                                    provider="fake", strategy=Strategy())
            assert event.body.items_sha256 == items_sha256(rebuilt.items)

    later_env = LocalEnvironment(rigs.workdir)
    later = await make_kit(Features(facts=True), later_env, memory)
    assert "Tests run with sh check.sh. (check.sh)" in later.profile
    assert later.system("base").startswith("base\n\nFacts remembered")
    (rigs.workdir / "check.sh").write_text("exit 2\n")
    changed = await make_kit(Features(facts=True), later_env, memory)
    assert changed.profile == ""


async def test_memory_search_leaves_out_stale_facts(rigs: RigFactory, tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    rig, _ = await facts_rig(rigs, [FakeTurn(items=(say("done"),)), FakeTurn(items=(say(FACT),))],
                             memory)
    await rig.agent.run("look around")
    parts = await memory_parts(Features(facts=True), LocalEnvironment(rigs.workdir), memory)
    [search] = parts.tools
    hit = await run_tool(search, make_ctx(rigs.workdir), query="how are tests run")
    assert hit.content.startswith("- [f") and "check.sh" in hit.content
    (rigs.workdir / "check.sh").write_text("exit 2\n")
    miss = await run_tool(search, make_ctx(rigs.workdir), query="tests")
    assert miss.content == (
        "no remembered facts match 'tests'\n(1 stale facts left out: their files changed)"
    )


async def test_a_fact_citing_no_existing_file_is_dropped(rigs: RigFactory, tmp_path: Path) -> None:
    reply = '{"fact": "Ghost.", "paths": ["nope.txt", "/etc/passwd"], "updates": null}'
    rig, _ = await facts_rig(rigs, [FakeTurn(items=(say("done"),)), FakeTurn(items=(say(reply),))],
                             tmp_path / "memory")
    await rig.agent.run("go")
    parts = await memory_parts(Features(facts=True), LocalEnvironment(rigs.workdir),
                               tmp_path / "memory")
    assert parts.profile == ""


async def test_notes_are_written_with_remember_and_shown_next_time(
    rigs: RigFactory, tmp_path: Path
) -> None:
    memory = tmp_path / "memory"
    env = LocalEnvironment(rigs.workdir)
    kit = await make_kit(Features(notes=True), env, memory)
    assert "remember" in kit.tools.names() and kit.hooks == () and kit.profile == ""
    script = [FakeTurn(items=(call("remember", text="Run  sh check.sh\nto test."),)),
              FakeTurn(items=(say("done"),))]
    rig = rigs.make(script, list(kit.tools), hooks=kit.hooks)
    assert (await rig.agent.run("go")).status == "done"
    later = await make_kit(Features(notes=True), env, memory)
    assert later.profile == (
        "Notes from earlier sessions in this repository:\n- Run sh check.sh to test.\n"
    )


async def test_no_memory_feature_means_no_memory(rigs: RigFactory, tmp_path: Path) -> None:
    kit = await make_kit(Features(), LocalEnvironment(rigs.workdir), tmp_path / "m")
    assert kit.profile == "" and kit.hooks == () and not (tmp_path / "m").exists()
