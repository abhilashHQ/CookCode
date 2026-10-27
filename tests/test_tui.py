from pathlib import Path

import pytest

pytest.importorskip("textual")

from harness.cli.app import parse_args
from harness.cli.tui import ApprovalScreen, CookApp
from harness.providers.fake import FakeProvider, FakeTurn, call, say


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(work)
    return work


def transcript(app: CookApp) -> str:
    from textual.widgets import RichLog

    return "\n".join(line.text for line in app.query_one("#log", RichLog).lines)


async def test_a_prompt_runs_and_the_answer_lands_in_the_transcript(repo: Path) -> None:
    app = CookApp(parse_args([]), FakeProvider([FakeTurn(items=(say("hello there"),))]))
    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"hi", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = transcript(app)
        assert "> hi" in text and "hello there" in text
        assert "1 requests" in str(app.query_one("#status").render())


async def test_a_mutating_call_asks_in_a_dialog(repo: Path) -> None:
    script = [FakeTurn(items=(call("write", path="a.txt", content="x\n"),)),
              FakeTurn(items=(say("wrote it"),))]
    app = CookApp(parse_args(["--mode", "ask"]), FakeProvider(script))
    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"write", "enter")
        for _ in range(50):
            await pilot.pause()
            if isinstance(app.screen, ApprovalScreen):
                break
        assert isinstance(app.screen, ApprovalScreen)
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert (repo / "a.txt").read_text() == "x\n"
        assert "wrote it" in transcript(app)
