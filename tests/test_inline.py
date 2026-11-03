import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent_helpers import wait_until
from cookcode.agent.tools import EXIT_PLAN
from cookcode.cli.app import parse_args
from cookcode.cli.inline import InlineApp
from cookcode.cli.render import Printer, call_label, summarize
from cookcode.core.items import ToolCall, ToolResult
from cookcode.providers.fake import FakeProvider, FakeTurn, call, say

ANSI_CODE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
DOWN, SHIFT_TAB, ESC = "\x1b[B", "\x1b[Z", "\x1b"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    monkeypatch.setenv("COOKCODE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(work)
    return work


class Session:
    def __init__(self, app: InlineApp, pipe: PipeInput, out: list[str]) -> None:
        self.app, self.pipe, self.out = app, pipe, out

    @property
    def text(self) -> str:
        return ANSI_CODE.sub("", "".join(self.out))

    async def type(self, text: str) -> None:
        self.pipe.send_text(text)
        await asyncio.sleep(0.15)

    async def until(self, predicate: object, timeout_s: float = 5.0) -> None:
        await wait_until(predicate, timeout_s)  # type: ignore[arg-type]

    async def idle(self) -> None:
        await wait_until(lambda: self.app.token is None)


@asynccontextmanager
async def session(argv: Sequence[str], script: list[FakeTurn]) -> AsyncIterator[Session]:
    with create_pipe_input() as pipe:
        out: list[str] = []
        app = InlineApp(
            parse_args(list(argv)), FakeProvider(script),
            input=pipe, output=DummyOutput(), write=out.append,
        )
        task = asyncio.create_task(app.run(patch=False))
        await wait_until(lambda: app.agent is not None and app.app.is_running)
        try:
            yield Session(app, pipe, out)
        finally:
            if app.app.is_running:
                app.app.exit(result=0)
            await asyncio.wait_for(task, 5)


async def test_a_prompt_prints_the_banner_the_prompt_and_the_answer(repo: Path) -> None:
    async with session([], [FakeTurn(items=(say("hello **there**"),))]) as s:
        await s.type("hi\r")
        await s.idle()
        await s.until(lambda: "hello" in s.text)
        assert "CookCode" in s.text
        assert "> hi" in s.text
        assert "● hello there" in s.text


async def test_an_edit_asks_in_a_menu_and_prints_its_diff(repo: Path) -> None:
    (repo / "a.txt").write_text("old\n")
    edit = call("edit", path="a.txt", old_string="old", new_string="new")
    script = [FakeTurn(items=(edit,)), FakeTurn(items=(say("done"),))]
    async with session(["--mode", "ask"], script) as s:
        await s.type("change it\r")
        await s.until(lambda: s.app.menu is not None)
        menu = s.app.menu
        assert menu is not None
        assert menu.question == "Do you want to make this edit to a.txt?"
        assert menu.options[0][0] == "Yes"
        await s.type("1")
        await s.idle()
        assert (repo / "a.txt").read_text() == "new\n"
        await s.until(lambda: "done" in s.text)
        assert "Update(a.txt)" in s.text
        assert "Updated a.txt with 1 addition and 1 removal" in s.text
        assert "- old" in s.text and "+ new" in s.text


async def test_no_takes_typed_feedback_back_to_the_model(repo: Path) -> None:
    write = call("write", path="a.txt", content="x\n")
    script = [FakeTurn(items=(write,)), FakeTurn(items=(say("ok"),))]
    async with session(["--mode", "ask"], script) as s:
        await s.type("write\r")
        await s.until(lambda: s.app.menu is not None)
        await s.type(ESC)
        await s.until(lambda: s.app.feedback is not None)
        await s.type("use b.txt\r")
        await s.idle()
        assert not (repo / "a.txt").exists()
        await s.until(lambda: "Denied: use b.txt" in s.text)


async def test_plan_review_approves_into_auto_edit(repo: Path) -> None:
    plan = call(EXIT_PLAN, plan="## Steps\n1. write a.txt")
    write = call("write", path="a.txt", content="x\n")
    script = [FakeTurn(items=(plan,)), FakeTurn(items=(write,)), FakeTurn(items=(say("done"),))]
    async with session(["--mode", "plan"], script) as s:
        assert "plan mode" in s.app._mode_line()[0][1]  # pyright: ignore[reportPrivateUsage]
        await s.type("make a.txt\r")
        await s.until(lambda: s.app.menu is not None)
        assert s.app.menu is not None
        assert s.app.menu.question == "Would you like to proceed?"
        assert "Plan" in s.text and "write a.txt" in s.text
        await s.type("1")
        await s.idle()
        assert (repo / "a.txt").read_text() == "x\n"
        assert s.app.mode == "auto-edit"
        await s.until(lambda: "done" in s.text)
        assert "User approved the plan · mode: auto-edit" in s.text
        assert "plan mode on" in s.text


async def test_plan_feedback_keeps_planning(repo: Path) -> None:
    plan = call(EXIT_PLAN, plan="rewrite everything")
    script = [FakeTurn(items=(plan,)), FakeTurn(items=(say("revising"),))]
    async with session(["--mode", "plan"], script) as s:
        await s.type("go\r")
        await s.until(lambda: s.app.menu is not None)
        await s.type(DOWN + DOWN + "\r")
        await s.until(lambda: s.app.feedback is not None)
        await s.type("smaller\r")
        await s.idle()
        await s.until(lambda: "revising" in s.text)
        assert "Kept planning: smaller" in s.text
        assert s.app.agent is not None and s.app.agent.mode == "plan"


async def test_shift_tab_and_slash_mode_change_the_mode(repo: Path) -> None:
    async with session(["--mode", "ask"], []) as s:
        await s.type(SHIFT_TAB)
        assert s.app.agent is not None and s.app.agent.mode == "auto-edit"
        await s.type(SHIFT_TAB)
        assert s.app.agent.mode == "plan"
        await s.type("/mode yolo\r")
        assert s.app.agent.mode == "yolo"
        await s.type("/mode nope\r")
        assert s.app.agent.mode == "yolo"
        await s.until(lambda: "pick a mode" in s.text)


def test_labels_name_the_tool_and_its_main_argument() -> None:
    def label(name: str, **arguments: object) -> str:
        return call_label(ToolCall(id="c", provider_call_id="p", name=name, arguments=arguments))

    assert label("read", path="a.py") == "Read(a.py)"
    assert label("edit", path="a.py", old_string="x", new_string="y") == "Update(a.py)"
    assert label("bash", command="git status") == "Bash(git status)"
    assert label("grep", pattern="TODO") == 'Search(pattern: "TODO", path: ".")'


def test_summaries_count_lines_and_trim_denials() -> None:
    read = ToolCall(id="c", provider_call_id="p", name="read", arguments={"path": "a"})
    ok = ToolResult(call_id="c", status="ok", content="1\ta\n2\tb\n")
    denied = ToolResult(
        call_id="c", status="denied", content="denied: plan mode: no. Nothing runs."
    )
    printer = Printer(lambda _: None, lambda: 80, color=False)
    assert printer.render(summarize(read, ok)[0]).strip() == "Read 2 lines"
    assert printer.render(summarize(read, denied)[0]).strip() == "Denied: plan mode: no."
