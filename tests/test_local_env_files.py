import os
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


async def test_write_goes_through_a_symlink_and_keeps_it(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"old")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    await LocalEnvironment(tmp_path).write_file("CLAUDE.md", b"new")
    assert (tmp_path / "CLAUDE.md").is_symlink()
    assert (tmp_path / "AGENTS.md").read_bytes() == b"new"


async def test_failed_write_leaves_no_temp_file(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    with pytest.raises(OSError):
        await LocalEnvironment(tmp_path).write_file("d", b"x")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d"]


async def test_new_files_get_umask_permissions(tmp_path: Path) -> None:
    umask = os.umask(0)
    os.umask(umask)
    await LocalEnvironment(tmp_path).write_file("new.txt", b"x")
    assert stat.S_IMODE((tmp_path / "new.txt").stat().st_mode) == 0o666 & ~umask


async def test_long_file_names_still_fit(tmp_path: Path) -> None:
    name = "n" * 250
    await LocalEnvironment(tmp_path).write_file(name, b"x")
    assert (tmp_path / name).read_bytes() == b"x"


async def test_remove_file_deletes_the_file_and_is_a_no_op_when_missing(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    await env.write_file("a/f.txt", b"x")
    await env.remove_file("a/f.txt")
    assert not (tmp_path / "a/f.txt").exists()
    await env.remove_file("a/f.txt")
    await env.remove_file("never/existed.txt")


async def test_remove_file_accepts_absolute_paths(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.txt"
    target.write_bytes(b"x")
    await LocalEnvironment(tmp_path / "root").remove_file(str(target))
    assert not target.exists()


def test_root_is_the_resolved_absolute_workspace(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert LocalEnvironment(tmp_path / "link").root == str((tmp_path / "real").resolve())
