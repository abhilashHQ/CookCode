import json
from pathlib import Path
from typing import Any, cast

import pytest
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from cookcode.core.usage import Usage
from cookcode.harbor.agent import CookCodeAgent, split_model
from cookcode.providers.fake import FakeProvider, FakeTurn, call, say
from test_harbor_env import LocalContainer


@pytest.fixture
def container_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "AGENTS.md").write_text("Run make test.\n")
    monkeypatch.chdir(root)
    return root


async def test_run_acts_in_the_container_and_reports_atif(
    tmp_path: Path, container_root: Path
) -> None:
    fake = FakeProvider(
        [
            FakeTurn(
                items=(call("bash", "call_1", command="echo done > out.txt"),),
                usage=Usage(input=50, cache_read=10, output=5),
            ),
            FakeTurn(items=(say("wrote out.txt"),), usage=Usage(input=70, output=3)),
        ]
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    agent = CookCodeAgent(logs_dir=logs, model_name="nim/some/model", provider=fake)
    context = AgentContext()
    await agent.run("write out.txt", cast(Any, LocalContainer()), context)

    assert (container_root / "out.txt").read_text() == "done\n"
    assert "Run make test." in (fake.requests[0].system or "")
    assert fake.requests[0].model == "some/model"
    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (
        130,
        10,
        8,
    )
    assert context.metadata is not None and context.metadata["status"] == "done"
    trajectory = Trajectory.model_validate(json.loads((logs / "trajectory.json").read_text()))
    assert trajectory.agent.name == "cookcode"
    assert trajectory.agent.tool_definitions is not None
    assert [s.source for s in trajectory.steps] == ["user", "agent", "agent"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("nim/minimaxai/minimax-m3.1-flash", ("nim", "minimaxai/minimax-m3.1-flash")),
        ("openrouter/cohere/north-mini-code:free", ("openrouter", "cohere/north-mini-code:free")),
        ("minimaxai/minimax-m3.1-flash", ("nim", "minimaxai/minimax-m3.1-flash")),
        (None, ("nim", "minimaxai/minimax-m3.1-flash")),
    ],
)
def test_split_model(name: str | None, expected: tuple[str, str]) -> None:
    assert split_model(name) == expected
