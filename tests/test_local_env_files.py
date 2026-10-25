import stat
from pathlib import Path

import pytest

from harness.env.local import LocalEnvironment


async def test_write_then_read_relative_path(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    await env.write_file("a/b/c.txt", b"hello")
    assert await env.read_file("a/b/c.txt") == b"hello"
    assert (tmp_path / "a/b/c.txt").read_bytes() == b"hello"


async def test_absolute_paths_are_used_as_given(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path / "root")
    target = tmp_path / "elsewhere.txt"
    await env.write_file(str(target), b"x")
    assert target.read_bytes() == b"x"


async def test_write_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    await env.write_file("f.txt", b"one")
    await env.write_file("f.txt", b"two")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]
    assert (tmp_path / "f.txt").read_bytes() == b"two"


async def test_write_keeps_the_executable_bit(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    await LocalEnvironment(tmp_path).write_file("run.sh", b"#!/bin/sh\necho bye\n")
    assert script.stat().st_mode & stat.S_IXUSR


async def test_reading_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await LocalEnvironment(tmp_path).read_file("missing.txt")
