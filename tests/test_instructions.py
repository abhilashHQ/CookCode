from pathlib import Path

from harness.agent.instructions import load_instructions, render_system
from harness.env.local import LocalEnvironment


async def test_root_files_come_first_then_the_working_directory(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("root agents")
    (tmp_path / "CLAUDE.md").write_text("root claude")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("pkg agents")
    files = await load_instructions(LocalEnvironment(tmp_path), "pkg")
    assert [(f.path, f.text) for f in files] == [
        ("AGENTS.md", "root agents"),
        ("CLAUDE.md", "root claude"),
        ("pkg/AGENTS.md", "pkg agents"),
    ]


async def test_identical_files_load_once(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("same")
    (tmp_path / "CLAUDE.md").symlink_to(tmp_path / "AGENTS.md")
    files = await load_instructions(LocalEnvironment(tmp_path))
    assert [f.path for f in files] == ["AGENTS.md"]


async def test_cwd_at_the_root_or_outside_it_reads_only_the_root(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("root")
    env = LocalEnvironment(tmp_path)
    assert [f.path for f in await load_instructions(env, ".")] == ["AGENTS.md"]
    assert [f.path for f in await load_instructions(env, "/etc")] == ["AGENTS.md"]


async def test_large_files_are_truncated_and_say_so(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 100)
    (file,) = await load_instructions(LocalEnvironment(tmp_path), max_bytes=10)
    assert file.text == "x" * 10 and file.truncated and file.size == 100
    assert "[truncated: the file has 100 bytes]" in render_system("base", [file])


async def test_a_directory_named_like_an_instruction_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()
    assert await load_instructions(LocalEnvironment(tmp_path)) == []


async def test_render_appends_sections_and_leaves_the_base_alone_without_files(
    tmp_path: Path,
) -> None:
    assert render_system("base", []) == "base"
    (tmp_path / "AGENTS.md").write_text("Run make test.\n")
    rendered = render_system("base", await load_instructions(LocalEnvironment(tmp_path)))
    assert rendered == "base\n\n# Project instructions\n\n## AGENTS.md\n\nRun make test.\n"
