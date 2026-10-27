from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import assert_every_call_resolved, bodies, echo_tool, kinds, results
from cookcode.agent.loop import Agent, AgentConfig
from cookcode.agent.tools import ToolRegistry
from cookcode.core.events import ModelRequestFinished, ProviderRetry
from cookcode.env.local import LocalEnvironment
from cookcode.store.jsonl import JsonlSessionLog, JsonlSessionStore
from openai_helpers import MODEL, Script, delta, ok, provider, sse, status, text_stream, tool_stream


@pytest.fixture
def log(tmp_path: Path) -> Iterator[JsonlSessionLog]:
    session = JsonlSessionStore(tmp_path / "sessions").open("s1", create=True)
    yield session
    session.close()


def agent(log: JsonlSessionLog, script: Script, tmp_path: Path) -> Agent:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return Agent(
        log=log,
        provider=provider(script),
        env=LocalEnvironment(work),
        tools=ToolRegistry([echo_tool()]),
        config=AgentConfig(model=MODEL, system_prompt="sys", mode="yolo"),
    )


async def test_a_tool_round_trip_replays_the_call_and_result_by_provider_id(
    log: JsonlSessionLog, tmp_path: Path
) -> None:
    script = Script(
        [ok(tool_stream("echo", '{"text": "pong"}', "call_xyz")), ok(text_stream("done"))]
    )
    outcome = await agent(log, script, tmp_path).run("ping")
    assert outcome.status == "done"
    assert_every_call_resolved(log.read())
    second = script.bodies[1]["messages"]
    assert second[-2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_xyz", "type": "function",
                        "function": {"name": "echo", "arguments": '{"text": "pong"}'}}],
    }
    assert second[-1] == {"role": "tool", "tool_call_id": "call_xyz", "content": "pong"}


async def test_retries_are_logged_under_the_request_and_the_response_lands_once(
    log: JsonlSessionLog, tmp_path: Path
) -> None:
    script = Script([
        status(429, "rate limited"),
        ok(sse([delta(content="half")], done=False)),
        ok(text_stream("whole")),
    ])
    outcome = await agent(log, script, tmp_path).run("hi")
    events = log.read()
    assert outcome.status == "done"
    retries = bodies(events, ProviderRetry)
    assert [(r.attempt, r.status) for r in retries] == [(1, 429), (2, None)]
    finished = bodies(events, ModelRequestFinished)
    assert len(finished) == 1
    assert {r.request_id for r in retries} == {finished[0].request_id}
    assert kinds(events).count("item") == 1


async def test_unparseable_arguments_get_a_clear_error_and_go_back_verbatim(
    log: JsonlSessionLog, tmp_path: Path
) -> None:
    script = Script([ok(tool_stream("echo", '{"text": ', "call_bad")), ok(text_stream("sorry"))])
    await agent(log, script, tmp_path).run("ping")
    (result,) = results(log.read())
    assert result.status == "error"
    assert "not a JSON object" in result.content
    replayed = script.bodies[1]["messages"]
    assert replayed[-2]["tool_calls"][0]["function"]["arguments"] == '{"text": '
    assert replayed[-1]["content"].startswith("[error] invalid arguments for echo")


async def test_a_rejected_request_ends_the_run_with_the_provider_message(
    log: JsonlSessionLog, tmp_path: Path
) -> None:
    script = Script([status(401, "bad key")])
    outcome = await agent(log, script, tmp_path).run("hi")
    assert outcome.status == "error"
    assert "bad key" in log.read()[-2].body.model_dump()["message"]
