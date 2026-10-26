from pathlib import Path

import pytest

from harness.core.cancel import CancelToken
from harness.tools.search import glob_tool, grep_tool
from tool_helpers import make_ctx, run_tool

BOTH = pytest.mark.parametrize("prefer_rg", [True, False], ids=["rg", "fallback"])


def _tree(root: Path) -> None:
    (root / "src" / "sub").mkdir(parents=True)
    (root / "x").mkdir()
    (root / "src" / "a.py").write_text("def foo():\n    pass\n")
    (root / "src" / "sub" / "b.py").write_text("foo = 1\nBAR = 2\n")
    (root / "README.md").write_text("foo docs\n")
    (root / "x" / "keep.txt").write_text("keep\n")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "h.py").write_text("foo hidden\n")


def test_declared_side_effects() -> None:
    assert grep_tool().side_effect == "read_only"
    assert glob_tool().side_effect == "read_only"
    assert grep_tool().touches is None


@BOTH
async def test_grep_reports_path_line_text(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(grep_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="foo")
    assert out.status == "ok"
    assert set(out.content.splitlines()) == {
        "src/a.py:1:def foo():",
        "src/sub/b.py:1:foo = 1",
        "README.md:1:foo docs",
    }


@BOTH
async def test_grep_glob_filter(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(
        grep_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="foo", glob="*.py"
    )
    assert set(out.content.splitlines()) == {"src/a.py:1:def foo():", "src/sub/b.py:1:foo = 1"}


@BOTH
async def test_grep_ignore_case(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    tool = grep_tool(prefer_rg=prefer_rg)
    sensitive = await run_tool(tool, make_ctx(tmp_path), pattern="bar")
    assert sensitive.content == "no matches"
    insensitive = await run_tool(tool, make_ctx(tmp_path), pattern="bar", ignore_case=True)
    assert insensitive.content == "src/sub/b.py:2:BAR = 2"


@BOTH
async def test_grep_no_match_is_ok(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(grep_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="zzzz")
    assert (out.status, out.exit_status, out.content) == ("ok", "ok", "no matches")


@BOTH
async def test_grep_path_argument_and_tracked_cwd(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    tool = grep_tool(prefer_rg=prefer_rg)
    scoped = await run_tool(tool, make_ctx(tmp_path), pattern="foo", path="src/sub")
    assert scoped.content == "src/sub/b.py:1:foo = 1"
    from_cwd = await run_tool(tool, make_ctx(tmp_path, cwd="src"), pattern="foo", glob="a.py")
    assert from_cwd.content == "a.py:1:def foo():"


@BOTH
async def test_grep_caps_results(tmp_path: Path, prefer_rg: bool) -> None:
    (tmp_path / "many.txt").write_text("".join(f"hit {i}\n" for i in range(30)))
    out = await run_tool(
        grep_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="hit", max_results=5
    )
    lines = out.content.splitlines()
    assert len(lines) == 6
    assert lines[0] == "many.txt:1:hit 0"
    assert lines[-1] == "[... 25 more matches]"


@BOTH
async def test_grep_invalid_regex_is_an_error_result(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(grep_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="foo(")
    assert out.status == "error"
    assert out.exit_status == "error"
    assert out.content


@BOTH
async def test_grep_output_is_capped_through_the_environment(
    tmp_path: Path, prefer_rg: bool
) -> None:
    (tmp_path / "big.txt").write_text("".join(f"needle {'x' * 80} {i}\n" for i in range(500)))
    out = await run_tool(
        grep_tool(prefer_rg=prefer_rg),
        make_ctx(tmp_path, max_output_bytes=2000),
        pattern="needle",
        max_results=1000,
    )
    assert out.truncated
    assert len(out.content.encode()) < 4000


@BOTH
async def test_grep_pattern_is_never_interpreted_by_the_shell(
    tmp_path: Path, prefer_rg: bool
) -> None:
    _tree(tmp_path)
    nasty = "'; rm -rf x; '"
    ctx = make_ctx(tmp_path)
    tool = grep_tool(prefer_rg=prefer_rg)
    await run_tool(tool, ctx, pattern=nasty)
    await run_tool(tool, ctx, pattern="foo", glob=nasty)
    await run_tool(tool, ctx, pattern="foo", path=nasty)
    assert (tmp_path / "x" / "keep.txt").exists()


@BOTH
async def test_grep_pattern_starting_with_a_dash(tmp_path: Path, prefer_rg: bool) -> None:
    (tmp_path / "f.txt").write_text("a -v b\n")
    out = await run_tool(grep_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="-v")
    assert out.content == "f.txt:1:a -v b"


@BOTH
async def test_grep_with_a_cancelled_token_reports_killed(
    tmp_path: Path, prefer_rg: bool
) -> None:
    _tree(tmp_path)
    token = CancelToken()
    token.cancel("user")
    out = await run_tool(
        grep_tool(prefer_rg=prefer_rg), make_ctx(tmp_path, cancel=token), pattern="foo"
    )
    assert out.exit_status == "killed"
    assert out.status == "error"


async def test_grep_fallback_gives_the_same_file_set_as_rg(tmp_path: Path) -> None:
    _tree(tmp_path)
    with_rg = await run_tool(grep_tool(), make_ctx(tmp_path), pattern="foo")
    without = await run_tool(grep_tool(prefer_rg=False), make_ctx(tmp_path), pattern="foo")
    assert set(with_rg.content.splitlines()) == set(without.content.splitlines())


@BOTH
async def test_glob_matches_recursive_patterns_sorted(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(
        glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="src/**/*.py"
    )
    assert out.status == "ok"
    assert out.content.splitlines() == ["src/a.py", "src/sub/b.py"]


@BOTH
async def test_glob_without_a_slash_matches_at_any_depth(
    tmp_path: Path, prefer_rg: bool
) -> None:
    _tree(tmp_path)
    tool = glob_tool(prefer_rg=prefer_rg)
    py = await run_tool(tool, make_ctx(tmp_path), pattern="*.py")
    assert py.content.splitlines() == ["src/a.py", "src/sub/b.py"]
    md = await run_tool(tool, make_ctx(tmp_path), pattern="*.md")
    assert md.content == "README.md"


@BOTH
async def test_glob_supports_braces(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="*.{py,md}")
    assert out.content.splitlines() == ["README.md", "src/a.py", "src/sub/b.py"]


@BOTH
async def test_glob_path_argument_prefixes_results(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    tool = glob_tool(prefer_rg=prefer_rg)
    scoped = await run_tool(tool, make_ctx(tmp_path), pattern="*.py", path="src")
    assert scoped.content.splitlines() == ["src/a.py", "src/sub/b.py"]
    from_cwd = await run_tool(tool, make_ctx(tmp_path, cwd="src"), pattern="**/*.py")
    assert from_cwd.content.splitlines() == ["a.py", "sub/b.py"]


@BOTH
async def test_glob_skips_hidden_directories(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="h.py")
    assert out.content == "no files matched"


@BOTH
async def test_glob_no_match_is_ok(tmp_path: Path, prefer_rg: bool) -> None:
    _tree(tmp_path)
    out = await run_tool(glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="*.rs")
    assert (out.status, out.content) == ("ok", "no files matched")


@BOTH
async def test_glob_caps_at_five_hundred(tmp_path: Path, prefer_rg: bool) -> None:
    (tmp_path / "many").mkdir()
    for i in range(510):
        (tmp_path / "many" / f"f{i:04d}.txt").write_text("")
    out = await run_tool(glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="*.txt")
    lines = out.content.splitlines()
    assert len(lines) == 501
    assert lines[0] == "many/f0000.txt"
    assert lines[499] == "many/f0499.txt"
    assert lines[500] == "[... 10 more]"


@BOTH
async def test_glob_missing_directory_is_an_error_result(
    tmp_path: Path, prefer_rg: bool
) -> None:
    out = await run_tool(
        glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path), pattern="*.py", path="nope"
    )
    assert out.status == "error"
    assert out.exit_status == "error"


@BOTH
async def test_glob_pattern_is_never_interpreted_by_the_shell(
    tmp_path: Path, prefer_rg: bool
) -> None:
    _tree(tmp_path)
    nasty = "'; rm -rf x; '"
    tool = glob_tool(prefer_rg=prefer_rg)
    await run_tool(tool, make_ctx(tmp_path), pattern=nasty)
    await run_tool(tool, make_ctx(tmp_path), pattern="*.py", path=nasty)
    assert (tmp_path / "x" / "keep.txt").exists()


async def test_glob_fallback_gives_the_same_file_set_as_rg(tmp_path: Path) -> None:
    _tree(tmp_path)
    for pattern in ("*.py", "src/**/*.py", "**/*.md", "src/*.py"):
        with_rg = await run_tool(glob_tool(), make_ctx(tmp_path), pattern=pattern)
        without = await run_tool(glob_tool(prefer_rg=False), make_ctx(tmp_path), pattern=pattern)
        assert with_rg.content == without.content, pattern


@BOTH
async def test_glob_path_starting_with_a_dash_is_a_directory_not_a_cd_option(
    tmp_path: Path, prefer_rg: bool
) -> None:
    tool = glob_tool(prefer_rg=prefer_rg)
    for path in ("-P", "-L", "-"):
        out = await run_tool(tool, make_ctx(tmp_path), pattern="*", path=path)
        assert (out.status, out.content) == ("error", f"directory not found: {path}")
    (tmp_path / "-P").mkdir()
    (tmp_path / "-P" / "in.txt").write_text("")
    out = await run_tool(tool, make_ctx(tmp_path), pattern="*", path="-P")
    assert out.content == "-P/in.txt"


@BOTH
async def test_glob_output_over_the_byte_cap_is_an_error_not_a_list(
    tmp_path: Path, prefer_rg: bool
) -> None:
    (tmp_path / "many").mkdir()
    for i in range(600):
        (tmp_path / "many" / f"{'n' * 40}{i:04d}.txt").write_text("")
    out = await run_tool(
        glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path, max_output_bytes=4000), pattern="*.txt"
    )
    assert (out.status, out.exit_status) == ("error", "error")
    assert "600 files matched" in out.content
    assert "truncated" not in out.content


@BOTH
async def test_glob_bounds_its_output_inside_the_command(tmp_path: Path, prefer_rg: bool) -> None:
    (tmp_path / "many").mkdir()
    for i in range(2000):
        (tmp_path / "many" / f"f{i:04d}.txt").write_text("")
    out = await run_tool(
        glob_tool(prefer_rg=prefer_rg), make_ctx(tmp_path, max_output_bytes=12_000), pattern="*"
    )
    assert out.status == "ok"
    lines = out.content.splitlines()
    assert lines[0] == "many/f0000.txt"
    assert lines[-1] == "[... 1500 more]"
    assert out.output_bytes is not None and out.output_bytes < 12_000


@BOTH
async def test_glob_path_is_never_resolved_through_oldpwd_or_cdpath(
    tmp_path: Path, prefer_rg: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "src").mkdir(parents=True)
    (elsewhere / "src" / "decoy.txt").write_text("")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("OLDPWD", str(elsewhere))
    monkeypatch.setenv("CDPATH", str(elsewhere))
    tool = glob_tool(prefer_rg=prefer_rg)
    dash = await run_tool(tool, make_ctx(root), pattern="*", path="-")
    assert (dash.status, dash.content) == ("error", "directory not found: -")
    src = await run_tool(tool, make_ctx(root), pattern="*", path="src")
    assert (src.status, src.content) == ("error", "directory not found: src")
