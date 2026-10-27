import asyncio
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_helpers import RigFactory, assert_every_call_resolved, bodies, results
from cookcode.core.cancel import CancelToken
from cookcode.core.events import ToolCallFinished
from cookcode.core.items import ToolResult
from cookcode.providers.fake import FakeTurn, call, say
from cookcode.tools import default_tools
from cookcode.tools.bash import bash_tool
from test_local_env_exec import _live_with_token  # pyright: ignore[reportPrivateUsage]
from tool_helpers import make_ctx, run_tool

SENTINEL = re.compile(r"__flagship_cwd_[0-9a-f]{32}__")


@pytest.fixture
def rigs(tmp_path: Path) -> Iterator[RigFactory]:
    factory = RigFactory(tmp_path)
    yield factory
    factory.close_all()


def test_bash_is_mutating_and_listed_in_the_default_tools() -> None:
    assert bash_tool().side_effect == "mutating"
    assert "bash" in [tool.name for tool in default_tools()]


def test_description_documents_the_shell_model() -> None:
    description = bash_tool().description
    assert "fresh shell" in description
    assert "cd" in description
    assert "exported variables" in description


async def test_echo_is_ok_and_leaves_the_cwd_alone(tmp_path: Path) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="echo hi")
    assert (out.status, out.exit_status) == ("ok", "ok")
    assert out.content == "hi"
    assert out.cwd == "."


async def test_cwd_stays_the_tracked_directory_when_the_command_does_not_cd(
    tmp_path: Path,
) -> None:
    (tmp_path / "sub").mkdir()
    out = await run_tool(bash_tool(), make_ctx(tmp_path, cwd="sub"), command="echo hi")
    assert out.content == "hi"
    assert out.cwd == "sub"


async def test_cd_is_reported_relative_to_the_environment_root(tmp_path: Path) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="mkdir -p a/b && cd a/b")
    assert out.status == "ok"
    assert out.cwd == "a/b"
    assert out.content == ""
    assert (tmp_path / "a" / "b").is_dir()


async def test_cd_relative_to_a_tracked_subdirectory(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    ctx = make_ctx(tmp_path, cwd="a")
    down = await run_tool(bash_tool(), ctx, command="cd b")
    assert down.cwd == "a/b"
    up = await run_tool(bash_tool(), ctx, command="cd ..")
    assert up.cwd == "."


async def test_cd_outside_the_root_stays_absolute(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    out = await run_tool(bash_tool(), make_ctx(root), command=f"cd {outside}")
    assert out.cwd == str(outside.resolve())


async def test_a_failing_cd_reports_the_error_and_keeps_the_cwd(tmp_path: Path) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="cd nowhere")
    assert out.status == "error"
    assert out.content.endswith("[exit code 1]")
    assert "nowhere" in out.content
    assert out.cwd == "."


async def test_exit_status_error_with_trailer_and_cwd_from_the_exit_trap(
    tmp_path: Path,
) -> None:
    (tmp_path / "sub").mkdir()
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="cd sub && exit 3")
    assert (out.status, out.exit_status) == ("error", "error")
    assert out.content == "[exit code 3]"
    assert out.cwd == "sub"


async def test_nonzero_exit_keeps_the_output_before_the_trailer(tmp_path: Path) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="echo partial; exit 2")
    assert out.status == "error"
    assert out.content == "partial\n[exit code 2]"


async def test_output_without_a_trailing_newline_is_kept_whole(tmp_path: Path) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="printf abc")
    assert out.content == "abc"


async def test_stderr_is_merged_into_the_output(tmp_path: Path) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="echo out; echo err >&2")
    assert out.content.splitlines() == ["out", "err"]


async def test_a_syntax_error_is_an_error_result_and_still_reports_the_cwd(
    tmp_path: Path,
) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="echo 'unterminated")
    assert out.status == "error"
    assert out.cwd == "."


async def test_a_command_cannot_spoof_the_cwd_with_sentinel_like_text(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    spoof = (
        "s=$(trap -p EXIT | grep -o '__flagship_cwd_[0-9a-f]*__' | head -1); "
        "printf '\\n%s\\n%s\\n%s\\n' \"$s\" /fake /fake; "
        "printf '\\n%s\\n%s\\n%s\\n' __flagship_cwd_deadbeef__ /fake /fake"
    )
    out = await run_tool(bash_tool(), make_ctx(tmp_path, cwd="sub"), command=spoof)
    assert out.cwd == "sub"
    assert "/fake" in out.content


async def test_the_sentinel_is_random_per_call(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    first = await run_tool(bash_tool(), ctx, command="trap -p EXIT")
    second = await run_tool(bash_tool(), ctx, command="trap -p EXIT")
    [one] = set(SENTINEL.findall(first.content))
    [two] = set(SENTINEL.findall(second.content))
    assert one != two


async def test_the_sentinel_never_appears_in_the_content(tmp_path: Path) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="echo visible")
    assert SENTINEL.search(out.content) is None
    assert str(tmp_path.resolve()) not in out.content


async def test_truncated_output_still_reports_the_cwd(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    out = await run_tool(
        bash_tool(),
        make_ctx(tmp_path, max_output_bytes=1500),
        command="cd sub; head -c 20000 /dev/zero | tr '\\0' x",
    )
    assert out.truncated
    assert out.output_bytes is not None
    assert out.output_bytes > 20000
    assert out.cwd == "sub"
    assert SENTINEL.search(out.content) is None
    assert len(out.content) < 1600


async def test_timeout_kills_background_children_and_reports_timeout(tmp_path: Path) -> None:
    token = f"77.{uuid.uuid4().int % 10**9}"
    out = await run_tool(
        bash_tool(),
        make_ctx(tmp_path, timeout_s=0.5),
        command=f"sleep {token} & wait",
    )
    assert out.exit_status == "timeout"
    assert out.status == "error"
    assert out.cwd is None
    for _ in range(60):
        if not _live_with_token(token):
            break
        await asyncio.sleep(0.05)
    assert _live_with_token(token) == []


async def test_the_tool_timeout_argument_is_capped_by_the_context(tmp_path: Path) -> None:
    out = await run_tool(
        bash_tool(), make_ctx(tmp_path, timeout_s=0.3), command="sleep 30", timeout_s=100
    )
    assert out.exit_status == "timeout"


async def test_a_smaller_timeout_argument_wins(tmp_path: Path) -> None:
    out = await run_tool(
        bash_tool(), make_ctx(tmp_path, timeout_s=30), command="sleep 30", timeout_s=0.3
    )
    assert out.exit_status == "timeout"


async def test_cancel_reports_killed_and_no_cwd(tmp_path: Path) -> None:
    token = CancelToken()
    task = asyncio.create_task(
        run_tool(bash_tool(), make_ctx(tmp_path, cancel=token), command="echo started; sleep 30")
    )
    await asyncio.sleep(0.3)
    token.cancel("user")
    out = await asyncio.wait_for(task, timeout=5)
    assert out.exit_status == "killed"
    assert out.status == "error"
    assert out.cwd is None


async def test_a_pre_cancelled_token_never_runs_the_command(tmp_path: Path) -> None:
    token = CancelToken()
    token.cancel("user")
    out = await run_tool(bash_tool(), make_ctx(tmp_path, cancel=token), command="touch ran.txt")
    assert out.exit_status == "killed"
    assert not (tmp_path / "ran.txt").exists()


async def test_cd_persists_through_the_agent_between_calls(rigs: RigFactory) -> None:
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command="mkdir -p a/b && cd a/b"),)),
            FakeTurn(items=(call("bash", command="pwd"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool()],
    )
    await rig.agent.run("go")
    events = rig.events()
    finished = bodies(events, ToolCallFinished)
    assert [f.cwd for f in finished] == ["a/b", "a/b"]
    pwd = results(events)[1]
    assert pwd.content == str((rig.workdir / "a" / "b").resolve())
    assert_every_call_resolved(events)


async def test_cwd_survives_a_resume_through_the_log(rigs: RigFactory) -> None:
    first = rigs.make(
        [
            FakeTurn(items=(call("bash", command="mkdir -p a/b && cd a/b"),)),
            FakeTurn(items=(say("ok"),)),
        ],
        [bash_tool()],
    )
    await first.agent.run("go")
    first.log.close()
    second = rigs.make(
        [FakeTurn(items=(call("bash", command="pwd"),)), FakeTurn(items=(say("ok"),))],
        [bash_tool()],
        log=rigs.open_log(),
    )
    await second.agent.run("again")
    assert results(second.events())[-1].content == str((second.workdir / "a" / "b").resolve())


async def test_exported_variables_do_not_persist_between_calls(rigs: RigFactory) -> None:
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command="export X=1"),)),
            FakeTurn(items=(call("bash", command='echo "[$X]"'),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool()],
    )
    await rig.agent.run("go")
    assert results(rig.events())[1].content == "[]"


async def test_exit_three_through_the_agent_is_an_error_result_with_the_new_cwd(
    rigs: RigFactory,
) -> None:
    (rigs.workdir / "sub").mkdir()
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command="cd sub && exit 3"),)),
            FakeTurn(items=(call("bash", command="pwd"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool()],
    )
    await rig.agent.run("go")
    events = rig.events()
    first, second = results(events)
    assert (first.status, first.content) == ("error", "[exit code 3]")
    assert second.content == str((rig.workdir / "sub").resolve())


async def test_a_timeout_through_the_agent_is_unknown(rigs: RigFactory) -> None:
    token = f"77.{uuid.uuid4().int % 10**9}"
    rig = rigs.make(
        [
            FakeTurn(items=(call("bash", command=f"sleep {token} & wait"),)),
            FakeTurn(items=(say("done"),)),
        ],
        [bash_tool()],
        tool_timeout_s=0.5,
    )
    await rig.agent.run("go")
    events = rig.events()
    [result] = results(events)
    assert isinstance(result, ToolResult)
    assert result.status == "unknown"
    [finished] = bodies(events, ToolCallFinished)
    assert finished.exit_status == "timeout"
    assert finished.cwd is None
    for _ in range(60):
        if not _live_with_token(token):
            break
        await asyncio.sleep(0.05)
    assert _live_with_token(token) == []
    assert_every_call_resolved(events)


async def test_a_deleted_working_directory_resets_to_the_root_without_running(
    tmp_path: Path,
) -> None:
    out = await run_tool(bash_tool(), make_ctx(tmp_path, cwd="gone"), command="touch marker")
    assert out.status == "error"
    assert out.cwd == "."
    assert "gone no longer exists" in out.content
    assert not (tmp_path / "marker").exists()
    after = await run_tool(bash_tool(), make_ctx(tmp_path, cwd=out.cwd), command="pwd")
    assert after.status == "ok"
    assert after.cwd == "."


async def test_leaving_the_workspace_and_coming_back_is_relative_again(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    out = await run_tool(bash_tool(), make_ctx(root), command=f"cd {outside}")
    assert out.cwd == str(outside.resolve())
    back = await run_tool(bash_tool(), make_ctx(root, cwd=str(outside)), command=f"cd {root}/sub")
    assert back.cwd == "sub"
    home = await run_tool(bash_tool(), make_ctx(root, cwd=str(outside)), command=f"cd {root}")
    assert home.cwd == "."


async def test_an_absolute_tracked_cwd_under_the_root_reports_relative(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    ctx = make_ctx(tmp_path, cwd=str(tmp_path.resolve() / "sub"))
    out = await run_tool(bash_tool(), ctx, command="pwd")
    assert out.content == str(tmp_path.resolve() / "sub")
    assert out.cwd == "sub"


async def test_a_symlinked_directory_is_reported_by_its_physical_path(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    out = await run_tool(bash_tool(), make_ctx(tmp_path), command="cd link")
    assert out.cwd == "real"


async def test_a_tracked_cwd_starting_with_a_dash_is_entered_literally(tmp_path: Path) -> None:
    (tmp_path / "-P").mkdir()
    out = await run_tool(bash_tool(), make_ctx(tmp_path, cwd="-P"), command="pwd")
    assert out.content == str(tmp_path.resolve() / "-P")
    assert out.cwd == "-P"


async def test_a_tracked_cwd_that_is_a_file_resets_without_running(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    out = await run_tool(bash_tool(), make_ctx(tmp_path, cwd="f"), command="touch marker")
    assert (out.status, out.cwd) == ("error", ".")
    assert not (tmp_path / "marker").exists()
    assert not (tmp_path / "f" / "marker").exists()


async def test_the_command_cannot_fake_the_cd_failure(tmp_path: Path) -> None:
    out = await run_tool(
        bash_tool(),
        make_ctx(tmp_path),
        command="trap -p; printf '%s\n' __flagship_nocwd_deadbeef__; exit 97",
    )
    assert out.status == "error"
    assert out.cwd == "."
    assert "no longer exists" not in out.content


async def test_the_tracked_cwd_is_never_resolved_through_oldpwd_or_cdpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "sub").mkdir(parents=True)
    root = tmp_path / "root"
    (root / "-").mkdir(parents=True)
    monkeypatch.setenv("OLDPWD", str(elsewhere))
    monkeypatch.setenv("CDPATH", str(elsewhere))
    dash = await run_tool(bash_tool(), make_ctx(root, cwd="-"), command="pwd")
    assert (dash.content, dash.cwd) == (str(root.resolve() / "-"), "-")
    missing = await run_tool(bash_tool(), make_ctx(root, cwd="sub"), command="touch marker")
    assert (missing.status, missing.cwd) == ("error", ".")
    assert not (elsewhere / "sub" / "marker").exists()
