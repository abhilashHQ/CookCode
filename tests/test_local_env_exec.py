import asyncio
import time
import uuid
from pathlib import Path

import pytest

from harness.core.cancel import CancelToken
from harness.env.base import Environment
from harness.env.local import LocalEnvironment, _CappedBuffer


def _is_dead(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"


async def _wait_dead(pid: int, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _is_dead(pid):
            return True
        await asyncio.sleep(0.05)
    return False


def test_local_environment_satisfies_the_protocol(tmp_path: Path) -> None:
    env: Environment = LocalEnvironment(tmp_path)
    assert env is not None


def test_capped_buffer_keeps_head_and_tail() -> None:
    buf = _CappedBuffer(10)
    for chunk in (b"abc", b"defgh", b"ijklmnop"):
        buf.feed(chunk)
    assert buf.total == 16
    assert buf.truncated
    assert buf.render() == "abcde\n[... 6 bytes truncated ...]\nlmnop"


def test_capped_buffer_untruncated_is_verbatim() -> None:
    buf = _CappedBuffer(10)
    buf.feed(b"0123456789")
    assert not buf.truncated
    assert buf.render() == "0123456789"


async def test_echo_and_exit_code(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    ok = await env.exec("echo hello", timeout_s=10, max_output_bytes=1000, cancel=CancelToken())
    assert (ok.exit_code, ok.output, ok.output_bytes) == (0, "hello\n", 6)
    bad = await env.exec("exit 3", timeout_s=10, max_output_bytes=1000, cancel=CancelToken())
    assert bad.exit_code == 3


async def test_stderr_is_merged_and_stdin_is_closed(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("echo out; echo err >&2; cat", timeout_s=5, max_output_bytes=1000,
                         cancel=CancelToken())
    assert res.exit_code == 0
    assert res.output == "out\nerr\n"


async def test_cwd_is_relative_to_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    env = LocalEnvironment(tmp_path)
    res = await env.exec("pwd", cwd="sub", timeout_s=5, max_output_bytes=1000, cancel=CancelToken())
    assert res.output.strip() == str((tmp_path / "sub").resolve())


async def test_large_output_is_truncated_head_and_tail(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("head -c 10000 /dev/zero | tr '\\0' a", timeout_s=10,
                         max_output_bytes=1000, cancel=CancelToken())
    assert res.truncated
    assert res.output_bytes == 10000
    assert res.output.startswith("a" * 500 + "\n[... 9000 bytes truncated ...]\n")
    assert res.output.endswith("a" * 500)


async def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("sleep 30 & echo $! > child.pid; wait", timeout_s=0.5,
                         max_output_bytes=1000, cancel=CancelToken())
    assert res.timed_out
    assert res.exit_code is None
    assert res.duration_s < 5
    assert await _wait_dead(int((tmp_path / "child.pid").read_text()))


async def test_background_children_do_not_outlive_the_command(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    res = await env.exec("sleep 30 & echo $! > child.pid", timeout_s=10, max_output_bytes=1000,
                         cancel=CancelToken())
    assert res.exit_code == 0
    assert res.duration_s < 5
    assert await _wait_dead(int((tmp_path / "child.pid").read_text()))


async def test_cancel_kills_a_running_command(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    cancel = CancelToken()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.2)
        cancel.cancel()

    canceller = asyncio.create_task(cancel_soon())
    res = await env.exec("sleep 30", timeout_s=10, max_output_bytes=1000, cancel=cancel)
    await canceller
    assert res.cancelled
    assert not res.timed_out
    assert res.exit_code is None
    assert res.duration_s < 5


async def test_already_cancelled_never_spawns(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    cancel = CancelToken()
    cancel.cancel()
    res = await env.exec("touch should-not-exist", timeout_s=5, max_output_bytes=1000,
                         cancel=cancel)
    assert res.cancelled
    assert not (tmp_path / "should-not-exist").exists()


async def test_cancelling_the_awaiting_task_kills_the_group(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    task = asyncio.create_task(
        env.exec("sleep 30 & echo $! > child.pid; wait", timeout_s=30, max_output_bytes=1000,
                 cancel=CancelToken())
    )
    pid_file = tmp_path / "child.pid"
    while not pid_file.exists() or not pid_file.read_text().strip():
        await asyncio.sleep(0.02)
    task.cancel()
    started = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 2
    assert await _wait_dead(int(pid_file.read_text()))


def test_capped_buffer_never_splits_a_character_when_nothing_was_dropped() -> None:
    buf = _CappedBuffer(10)
    buf.feed("aaaa€bb".encode())
    assert not buf.truncated
    assert buf.render() == "aaaa€bb"


def test_capped_buffer_trims_partial_characters_at_the_cut() -> None:
    buf = _CappedBuffer(10)
    buf.feed("aaaa€".encode() + b"x" * 20 + "€bbb".encode())
    out = buf.render()
    assert "�" not in out
    head, marker, tail = out.split("\n")
    assert head == "aaaa"
    assert tail == "bbb"
    assert marker == f"[... {buf.total - 7} bytes truncated ...]"


@pytest.mark.parametrize("cap", [0, 1, 2, 3])
def test_capped_buffer_respects_tiny_caps(cap: int) -> None:
    buf = _CappedBuffer(cap)
    buf.feed(b"abcdef")
    kept = len(buf._head) + len(buf._tail)  # pyright: ignore[reportPrivateUsage]
    assert kept <= cap


def _live_with_token(token: str) -> list[int]:
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            continue
        if token.encode() in cmdline and state != "Z":
            pids.append(int(entry.name))
    return pids


async def test_cancelling_at_any_point_after_start_leaks_no_process(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    token = f"77.{uuid.uuid4().int % 10**9}"
    for yields in range(15):
        task = asyncio.create_task(
            env.exec(f"sleep {token} & wait", timeout_s=30, max_output_bytes=100,
                     cancel=CancelToken())
        )
        for _ in range(yields):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    deadline = time.monotonic() + 3
    while _live_with_token(token) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert _live_with_token(token) == []
