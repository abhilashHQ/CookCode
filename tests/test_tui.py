from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("textual")

from textual.pilot import Pilot

from cookcode.agent.tools import EXIT_PLAN
from cookcode.cli.app import parse_args
from cookcode.cli.tui import ApprovalScreen, CookApp, FeedbackScreen, PlanScreen
from cookcode.cli.tui_render import AssistantText, PlanBlock, ToolBlock
from cookcode.providers.fake import FakeProvider, FakeTurn, call, say


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.setenv("COOKCODE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(work)
    return work


async def send(pilot: Pilot[Any], text: str) -> None:
    await pilot.click("#prompt")
    await pilot.press(*text, "enter")


async def until_screen(pilot: Pilot[Any], kind: type) -> None:
    for _ in range(100):
        await pilot.pause()
        if isinstance(pilot.app.screen, kind):
            return
    raise AssertionError(f"{kind.__name__} never opened")


async def settle(app: CookApp, pilot: Pilot[Any]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_a_prompt_runs_and_the_answer_lands_in_the_transcript(repo: Path) -> None:
    app = CookApp(parse_args([]), FakeProvider([FakeTurn(items=(say("hello **there**"),))]))
    async with app.run_test(size=(120, 40)) as pilot:
        await send(pilot, "hi")
        await settle(app, pilot)
        text = app.transcript.plain
        assert "> hi" in text and "hello **there**" in text
        answers = list(app.transcript.query(AssistantText))
        assert len(answers) == 1 and answers[0].done
        assert "1 requests" in str(app.query_one("#status").render())


async def test_a_mutating_call_asks_in_a_dialog_and_shows_its_diff(repo: Path) -> None:
    script = [FakeTurn(items=(call("write", path="a.txt", content="x\n"),)),
              FakeTurn(items=(say("wrote it"),))]
    app = CookApp(parse_args(["--mode", "ask"]), FakeProvider(script))
    async with app.run_test(size=(120, 40)) as pilot:
        await send(pilot, "write")
        await until_screen(pilot, ApprovalScreen)
        await pilot.press("y")
        await settle(app, pilot)
        assert (repo / "a.txt").read_text() == "x\n"
        (block,) = app.transcript.query(ToolBlock)
        assert block.status == "ok"
        assert not block.collapsed
        assert list(app.files) == ["a.txt"]
        assert "wrote it" in app.transcript.plain


async def test_a_denial_sends_the_typed_reason_to_the_model(repo: Path) -> None:
    write = call("write", path="a.txt", content="x\n")
    app = CookApp(
        parse_args(["--mode", "ask"]),
        FakeProvider([FakeTurn(items=(write,)), FakeTurn(items=(say("ok"),))]),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await send(pilot, "write")
        await until_screen(pilot, ApprovalScreen)
        await pilot.press("n")
        await until_screen(pilot, FeedbackScreen)
        await pilot.press(*"use b.txt", "enter")
        await settle(app, pilot)
        (block,) = app.transcript.query(ToolBlock)
        assert block.status == "denied"
        assert "use b.txt" in block.plain
        assert not (repo / "a.txt").exists()


async def test_plan_review_approves_and_unlocks_edits(repo: Path) -> None:
    plan = call(EXIT_PLAN, plan="# Plan\n1. write a.txt")
    write = call("write", path="a.txt", content="x\n")
    script = [FakeTurn(items=(plan,)), FakeTurn(items=(write,)), FakeTurn(items=(say("done"),))]
    app = CookApp(parse_args(["--mode", "plan"]), FakeProvider(script))
    async with app.run_test(size=(120, 40)) as pilot:
        await send(pilot, "make a.txt")
        await until_screen(pilot, PlanScreen)
        await pilot.press("a")
        await settle(app, pilot)
        assert (repo / "a.txt").read_text() == "x\n"
        (block,) = app.transcript.query(PlanBlock)
        assert block.has_class("approved")
        assert app.mode == "auto-edit"
        assert "[mode] start -> plan" in app.transcript.plain
        assert "auto-edit" in str(app.query_one("#status").render())


async def test_plan_review_feedback_keeps_planning(repo: Path) -> None:
    plan = call(EXIT_PLAN, plan="rewrite everything")
    script = [FakeTurn(items=(plan,)), FakeTurn(items=(say("revising"),))]
    app = CookApp(parse_args(["--mode", "plan"]), FakeProvider(script))
    async with app.run_test(size=(120, 40)) as pilot:
        await send(pilot, "go")
        await until_screen(pilot, PlanScreen)
        await pilot.press("n")
        await until_screen(pilot, FeedbackScreen)
        await pilot.press(*"smaller", "enter")
        await settle(app, pilot)
        (block,) = app.transcript.query(PlanBlock)
        assert block.has_class("rejected")
        assert app.mode == "plan"
        assert app.agent is not None and app.agent.mode == "plan"


async def test_shift_tab_and_slash_mode_set_the_next_runs_mode(repo: Path) -> None:
    app = CookApp(parse_args(["--mode", "ask"]), FakeProvider([]))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("shift+tab")
        assert app.agent is not None and app.agent.mode == "auto-edit"
        await pilot.press("shift+tab")
        assert app.agent.mode == "plan"
        await send(pilot, "/mode yolo")
        await pilot.pause()
        assert app.agent.mode == "yolo"
        await send(pilot, "/mode nope")
        await pilot.pause()
        assert app.agent.mode == "yolo"
        assert "pick one of" in app.transcript.plain
