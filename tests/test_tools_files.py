from pathlib import Path

import pytest

from harness.tools import default_tools
from harness.tools.files import edit_tool, read_tool, write_tool
from tool_helpers import make_ctx, run_tool


def test_declared_side_effects_and_touches() -> None:
    assert read_tool().side_effect == "read_only"
    assert write_tool().side_effect == "mutating"
    assert edit_tool().side_effect == "mutating"
    assert read_tool().touches is None
    write = write_tool()
    edit = edit_tool()
    assert write.touches is not None
    assert edit.touches is not None
    assert write.touches(write.args_model(path="a/b.py", content="x")) == ["a/b.py"]
    assert edit.touches(edit.args_model(path="c.py", old_string="a", new_string="b")) == ["c.py"]


def test_default_tools_names_are_unique_and_include_the_file_tools() -> None:
    names = [tool.name for tool in default_tools()]
    assert len(names) == len(set(names))
    assert {"read", "write", "edit", "grep", "glob"} <= set(names)


async def test_read_numbers_lines_cat_n_style(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha\nbeta\n\ngamma\n")
    out = await run_tool(read_tool(), make_ctx(tmp_path), path="a.txt")
    assert out.status == "ok"
    assert out.content == "     1\talpha\n     2\tbeta\n     3\t\n     4\tgamma"


async def test_read_keeps_a_final_line_without_a_newline(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo")
    out = await run_tool(read_tool(), make_ctx(tmp_path), path="a.txt")
    assert out.content == "     1\tone\n     2\ttwo"


async def test_read_window_and_continuation_hint(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("".join(f"line{i}\n" for i in range(1, 11)))
    out = await run_tool(read_tool(), make_ctx(tmp_path), path="a.txt", offset=3, limit=4)
    assert out.content.splitlines() == [
        "     3\tline3",
        "     4\tline4",
        "     5\tline5",
        "     6\tline6",
        "[... 4 more lines. Use offset=7 to continue.]",
    ]
    last = await run_tool(read_tool(), make_ctx(tmp_path), path="a.txt", offset=7, limit=4)
    assert last.content.splitlines()[-1] == "    10\tline10"
    assert "more lines" not in last.content


async def test_read_default_limit_is_two_thousand_lines(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("".join(f"{i}\n" for i in range(1, 2503)))
    out = await run_tool(read_tool(), make_ctx(tmp_path), path="big.txt")
    lines = out.content.splitlines()
    assert lines[1999] == "  2000\t2000"
    assert lines[2000] == "[... 502 more lines. Use offset=2001 to continue.]"
    assert len(lines) == 2001


async def test_read_cuts_long_lines(tmp_path: Path) -> None:
    (tmp_path / "w.txt").write_text("x" * 2500 + "\nshort\n")
    out = await run_tool(read_tool(), make_ctx(tmp_path), path="w.txt")
    first, second = out.content.splitlines()
    assert first == "     1\t" + "x" * 2000 + " [... line truncated]"
    assert second == "     2\tshort"


async def test_read_does_not_cut_a_line_of_exactly_the_limit(tmp_path: Path) -> None:
    (tmp_path / "w.txt").write_text("x" * 2000 + "\n")
    out = await run_tool(read_tool(), make_ctx(tmp_path), path="w.txt")
    assert out.content == "     1\t" + "x" * 2000


async def test_read_errors(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "bin").write_bytes(b"\xff\xfe\x00\x80")
    (tmp_path / "small.txt").write_text("a\nb\n")
    ctx = make_ctx(tmp_path)
    missing = await run_tool(read_tool(), ctx, path="nope.txt")
    assert (missing.status, missing.content) == ("error", "file not found: nope.txt")
    directory = await run_tool(read_tool(), ctx, path="d")
    assert (directory.status, directory.content) == ("error", "is a directory: d")
    binary = await run_tool(read_tool(), ctx, path="bin")
    assert (binary.status, binary.content) == ("error", "not a text file: bin")
    past = await run_tool(read_tool(), ctx, path="small.txt", offset=5)
    assert past.status == "error"
    assert "offset 5" in past.content


async def test_read_empty_file(tmp_path: Path) -> None:
    (tmp_path / "e.txt").write_text("")
    out = await run_tool(read_tool(), make_ctx(tmp_path), path="e.txt")
    assert (out.status, out.content) == ("ok", "[empty file]")


async def test_read_resolves_relative_paths_against_the_tracked_cwd(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("inside\n")
    (tmp_path / "f.txt").write_text("outside\n")
    out = await run_tool(read_tool(), make_ctx(tmp_path, cwd="sub"), path="f.txt")
    assert out.content == "     1\tinside"
    up = await run_tool(read_tool(), make_ctx(tmp_path, cwd="sub"), path="../f.txt")
    assert up.content == "     1\toutside"


async def test_read_uses_absolute_paths_as_given(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "f.txt").write_text("abs\n")
    ctx = make_ctx(tmp_path / "root", cwd="sub")
    out = await run_tool(read_tool(), ctx, path=str(other / "f.txt"))
    assert out.content == "     1\tabs"


async def test_write_creates_parents_and_reports_bytes(tmp_path: Path) -> None:
    out = await run_tool(write_tool(), make_ctx(tmp_path), path="a/b/c.txt", content="héllo\n")
    assert out.status == "ok"
    assert out.content == "wrote 7 bytes to a/b/c.txt"
    assert (tmp_path / "a/b/c.txt").read_bytes() == "héllo\n".encode()


async def test_write_overwrites(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("old content")
    out = await run_tool(write_tool(), make_ctx(tmp_path), path="f.txt", content="new")
    assert out.content == "wrote 3 bytes to f.txt"
    assert (tmp_path / "f.txt").read_text() == "new"


async def test_write_resolves_against_the_tracked_cwd(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    await run_tool(write_tool(), make_ctx(tmp_path, cwd="sub"), path="f.txt", content="x")
    assert (tmp_path / "sub" / "f.txt").read_text() == "x"
    assert not (tmp_path / "f.txt").exists()


async def test_write_to_a_directory_is_an_error_result_not_a_raise(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    out = await run_tool(write_tool(), make_ctx(tmp_path), path="d", content="x")
    assert out.status == "error"
    assert out.exit_status == "error"
    assert out.content.startswith("cannot write d:")
    assert (tmp_path / "d").is_dir()


async def test_edit_replaces_a_unique_match(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
    out = await run_tool(
        edit_tool(), make_ctx(tmp_path), path="f.py", old_string="return 2", new_string="return 3"
    )
    assert (out.status, out.content) == ("ok", "edited f.py: replaced 1 occurrence(s)")
    assert (tmp_path / "f.py").read_text() == "def a():\n    return 1\n\ndef b():\n    return 3\n"


async def test_edit_replace_all(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x y x z x")
    out = await run_tool(
        edit_tool(),
        make_ctx(tmp_path),
        path="f.txt",
        old_string="x",
        new_string="w",
        replace_all=True,
    )
    assert out.content == "edited f.txt: replaced 3 occurrence(s)"
    assert (tmp_path / "f.txt").read_text() == "w y w z w"


async def test_edit_preserves_crlf_and_other_bytes(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    await run_tool(edit_tool(), make_ctx(tmp_path), path="f.txt", old_string="two", new_string="2")
    assert (tmp_path / "f.txt").read_bytes() == b"one\r\n2\r\nthree\r\n"


ORIGINAL = b"alpha\n  beta\nalpha\n"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"path": "gone.txt", "old_string": "a", "new_string": "b"},
            "file not found: gone.txt",
        ),
        (
            {"path": "f.txt", "old_string": "alpha", "new_string": "alpha"},
            "old_string and new_string are identical",
        ),
        (
            {"path": "f.txt", "old_string": "gamma", "new_string": "delta"},
            "old_string not found in f.txt",
        ),
        (
            {"path": "f.txt", "old_string": "alpha", "new_string": "omega"},
            "old_string matches 2 times in f.txt; "
            "add surrounding context to make it unique, or set replace_all",
        ),
        (
            {"path": "f.txt", "old_string": "", "new_string": "x"},
            "old_string must not be empty",
        ),
        (
            {"path": "d", "old_string": "a", "new_string": "b"},
            "is a directory: d",
        ),
    ],
)
async def test_edit_errors_leave_the_file_byte_identical(
    tmp_path: Path, arguments: dict[str, object], message: str
) -> None:
    (tmp_path / "f.txt").write_bytes(ORIGINAL)
    (tmp_path / "d").mkdir()
    out = await run_tool(edit_tool(), make_ctx(tmp_path), **arguments)
    assert out.status == "error"
    assert out.exit_status == "error"
    assert out.content == message
    assert (tmp_path / "f.txt").read_bytes() == ORIGINAL


async def test_edit_hints_when_only_whitespace_differs(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_bytes(ORIGINAL)
    out = await run_tool(
        edit_tool(),
        make_ctx(tmp_path),
        path="f.txt",
        old_string="alpha beta",
        new_string="x",
    )
    assert out.status == "error"
    assert out.content == (
        "old_string not found in f.txt (a match exists with different whitespace; "
        "copy the exact text from read)"
    )
    assert (tmp_path / "f.txt").read_bytes() == ORIGINAL


async def test_edit_on_a_binary_file_errors_without_touching_it(tmp_path: Path) -> None:
    (tmp_path / "bin").write_bytes(b"\xff\xfe\x00\x80")
    out = await run_tool(
        edit_tool(), make_ctx(tmp_path), path="bin", old_string="a", new_string="b"
    )
    assert (out.status, out.content) == ("error", "not a text file: bin")
    assert (tmp_path / "bin").read_bytes() == b"\xff\xfe\x00\x80"


async def test_edit_resolves_against_the_tracked_cwd(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("hello")
    await run_tool(
        edit_tool(),
        make_ctx(tmp_path, cwd="sub"),
        path="f.txt",
        old_string="hello",
        new_string="bye",
    )
    assert (tmp_path / "sub" / "f.txt").read_text() == "bye"


async def test_read_stops_at_the_output_cap_with_a_continuation_hint(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("".join(f"{'x' * 90}\n" for _ in range(100)))
    out = await run_tool(read_tool(), make_ctx(tmp_path, max_output_bytes=1000), path="a.txt")
    lines = out.content.splitlines()
    assert len(lines) == 11
    assert lines[-1] == "[... 90 more lines. Use offset=11 to continue.]"
    assert len("\n".join(lines[:-1]).encode()) <= 1000


async def test_read_returns_at_least_one_line_under_a_tiny_cap(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a long first line\nsecond\n")
    out = await run_tool(read_tool(), make_ctx(tmp_path, max_output_bytes=5), path="a.txt")
    assert out.content.splitlines() == [
        "     1\ta long first line",
        "[... 1 more lines. Use offset=2 to continue.]",
    ]
