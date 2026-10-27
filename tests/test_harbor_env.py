import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from cookcode.core.cancel import CancelToken
from cookcode.env.harbor import HarborEnvironment
from cookcode.env.local import LocalEnvironment


@dataclass(frozen=True)
class Result:
    stdout: str | None
    return_code: int


class LocalContainer:
    """Runs container execs as local processes. Like docker exec, a timeout or cancel kills
    only the client process, never what it started."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> Result:
        self.commands.append(command)
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout_sec)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        return Result(out.decode(errors="replace") or None, proc.returncode or 0)

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        shutil.copyfile(source_path, target_path)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    return work


@pytest.fixture
def env(root: Path) -> HarborEnvironment:
    return HarborEnvironment(LocalContainer(), str(root))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_dead(pid: int) -> bool:
    for _ in range(100):
        if not alive(pid):
            return True
        await asyncio.sleep(0.02)
    return False


COMMANDS = [
    "echo hi; echo err >&2; exit 3",
    "pwd",
    "head -c 70000 /dev/zero | tr '\\0' a; printf 'é%.0s' $(seq 3000)",
    "printf 'no newline'",
    "true",
]


@pytest.mark.parametrize("cmd", COMMANDS)
@pytest.mark.parametrize("cwd", [".", "sub"])
async def test_exec_matches_the_local_environment(
    root: Path, env: HarborEnvironment, cmd: str, cwd: str
) -> None:
    local = LocalEnvironment(root)
    expected = await local.exec(
        cmd, cwd=cwd, timeout_s=10, max_output_bytes=1000, cancel=CancelToken()
    )
    got = await env.exec(cmd, cwd=cwd, timeout_s=10, max_output_bytes=1000, cancel=CancelToken())
    fields = ("exit_code", "output", "output_bytes", "truncated", "timed_out", "cancelled")
    assert [getattr(got, f) for f in fields] == [getattr(expected, f) for f in fields]


async def test_timeout_kills_the_whole_group(root: Path, env: HarborEnvironment) -> None:
    cmd = "sleep 30 & echo $! > bg.pid; echo started; sleep 30"
    result = await env.exec(cmd, timeout_s=0.5, max_output_bytes=1000, cancel=CancelToken())
    assert (result.timed_out, result.exit_code) == (True, None)
    assert "started" in result.output
    assert await wait_dead(int((root / "bg.pid").read_text()))


async def test_cancel_kills_the_whole_group(root: Path, env: HarborEnvironment) -> None:
    cancel = CancelToken()
    cmd = "sleep 30 & echo $! > bg.pid; sleep 30"
    running = asyncio.create_task(
        env.exec(cmd, timeout_s=30, max_output_bytes=1000, cancel=cancel)
    )
    while not (root / "bg.pid").exists():
        await asyncio.sleep(0.02)
    cancel.cancel("user")
    result = await asyncio.wait_for(running, 15)
    assert (result.cancelled, result.timed_out, result.exit_code) == (True, False, None)
    assert await wait_dead(int((root / "bg.pid").read_text()))


async def test_background_children_die_when_the_command_ends(
    root: Path, env: HarborEnvironment
) -> None:
    result = await env.exec(
        "sleep 30 & echo $! > bg.pid", timeout_s=10, max_output_bytes=1000, cancel=CancelToken()
    )
    assert result.exit_code == 0
    assert await wait_dead(int((root / "bg.pid").read_text()))


async def test_cancelled_before_start_runs_nothing(env: HarborEnvironment) -> None:
    cancel = CancelToken()
    cancel.cancel("user")
    result = await env.exec("touch x", timeout_s=5, max_output_bytes=10, cancel=cancel)
    assert result.cancelled and not (Path(env.root) / "x").exists()


async def test_files_round_trip_past_the_argv_limit(root: Path, env: HarborEnvironment) -> None:
    data = os.urandom(300_000)
    await env.write_file("deep/new/blob.bin", data)
    assert (root / "deep/new/blob.bin").read_bytes() == data
    assert await env.read_file("deep/new/blob.bin") == data
    assert await env.read_file(str(root / "deep/new/blob.bin")) == data


async def test_read_errors_match_local(root: Path, env: HarborEnvironment) -> None:
    with pytest.raises(FileNotFoundError):
        await env.read_file("missing.txt")
    with pytest.raises(IsADirectoryError):
        await env.read_file("sub")


async def test_write_keeps_mode_and_follows_symlinks(root: Path, env: HarborEnvironment) -> None:
    script = root / "run.sh"
    script.write_text("old")
    script.chmod(0o755)
    (root / "link.sh").symlink_to(script)
    await env.write_file("link.sh", b"new")
    assert script.read_text() == "new" and (root / "link.sh").is_symlink()
    assert script.stat().st_mode & 0o777 == 0o755
    assert [p.name for p in root.iterdir() if p.name.endswith(".tmp")] == []


async def test_remove_is_idempotent(root: Path, env: HarborEnvironment) -> None:
    (root / "gone.txt").write_text("x")
    await env.remove_file("gone.txt")
    await env.remove_file("gone.txt")
    assert not (root / "gone.txt").exists()
