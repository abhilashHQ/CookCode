import posixpath
import re

from pydantic import BaseModel, Field

from harness.agent.tools import Tool, ToolContext, ToolOutput

MAX_LINE_CHARS = 2000
LINE_TRUNCATED = " [... line truncated]"


def resolve_path(cwd: str, path: str) -> str:
    if posixpath.isabs(path):
        return path
    return posixpath.normpath(posixpath.join(cwd, path))


def _error(message: str) -> ToolOutput:
    return ToolOutput(content=message, status="error", exit_status="error")


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


async def _read_text(ctx: ToolContext, path: str) -> str | ToolOutput:
    try:
        data = await ctx.env.read_file(resolve_path(ctx.cwd, path))
    except (FileNotFoundError, NotADirectoryError):
        return _error(f"file not found: {path}")
    except IsADirectoryError:
        return _error(f"is a directory: {path}")
    except Exception as exc:
        return _error(f"cannot read {path}: {_describe(exc)}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return _error(f"not a text file: {path}")


class ReadArgs(BaseModel):
    path: str = Field(description="File to read, relative to the working directory or absolute.")
    offset: int = Field(default=1, ge=1, description="1-based line to start from.")
    limit: int = Field(default=2000, ge=1, description="Maximum number of lines to return.")


def _numbered(lines: list[str], start: int, max_bytes: int) -> list[str]:
    numbered: list[str] = []
    size = 0
    for number, line in enumerate(lines, start=start):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + LINE_TRUNCATED
        entry = f"{number:>6}\t{line}"
        size += len(entry.encode()) + 1
        if numbered and size > max_bytes:
            break
        numbered.append(entry)
    return numbered


async def _read(args: ReadArgs, ctx: ToolContext) -> ToolOutput:
    text = await _read_text(ctx, args.path)
    if isinstance(text, ToolOutput):
        return text
    if text == "":
        return ToolOutput(content="[empty file]")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    first = args.offset - 1
    if first >= len(lines):
        return _error(f"offset {args.offset} is past the end of {args.path} ({len(lines)} lines)")
    out = _numbered(lines[first : first + args.limit], args.offset, ctx.max_output_bytes)
    remaining = len(lines) - (first + len(out))
    if remaining > 0:
        next_offset = args.offset + len(out)
        out.append(f"[... {remaining} more lines. Use offset={next_offset} to continue.]")
    return ToolOutput(content="\n".join(out))


def read_tool() -> Tool[ReadArgs]:
    return Tool(
        name="read",
        description=(
            "Read a text file with line numbers. Use offset and limit to page through large "
            "files. Lines over 2000 characters are cut, and output stops at the output cap with "
            "a hint giving the offset to continue from."
        ),
        args_model=ReadArgs,
        side_effect="read_only",
        handler=_read,
    )


class WriteArgs(BaseModel):
    path: str = Field(description="File to write, relative to the working directory or absolute.")
    content: str = Field(description="Full new content of the file.")


async def _write(args: WriteArgs, ctx: ToolContext) -> ToolOutput:
    data = args.content.encode()
    try:
        await ctx.env.write_file(resolve_path(ctx.cwd, args.path), data)
    except Exception as exc:
        return _error(f"cannot write {args.path}: {_describe(exc)}")
    return ToolOutput(content=f"wrote {len(data)} bytes to {args.path}")


def write_tool() -> Tool[WriteArgs]:
    return Tool(
        name="write",
        description=(
            "Create a file or overwrite it with the given content. Parent directories are "
            "created. Prefer edit for changes to an existing file."
        ),
        args_model=WriteArgs,
        side_effect="mutating",
        handler=_write,
        touches=lambda args: [args.path],
    )


class EditArgs(BaseModel):
    path: str = Field(description="File to edit, relative to the working directory or absolute.")
    old_string: str = Field(description="Exact text to replace, including whitespace.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence.")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


async def _edit(args: EditArgs, ctx: ToolContext) -> ToolOutput:
    text = await _read_text(ctx, args.path)
    if isinstance(text, ToolOutput):
        return text
    if args.old_string == args.new_string:
        return _error("old_string and new_string are identical")
    if args.old_string == "":
        return _error("old_string must not be empty")
    count = text.count(args.old_string)
    if count == 0:
        hint = ""
        needle = _collapse(args.old_string)
        if needle and needle in _collapse(text):
            hint = " (a match exists with different whitespace; copy the exact text from read)"
        return _error(f"old_string not found in {args.path}{hint}")
    if count > 1 and not args.replace_all:
        return _error(
            f"old_string matches {count} times in {args.path}; "
            "add surrounding context to make it unique, or set replace_all"
        )
    updated = text.replace(args.old_string, args.new_string)
    try:
        await ctx.env.write_file(resolve_path(ctx.cwd, args.path), updated.encode())
    except Exception as exc:
        return _error(f"cannot write {args.path}: {_describe(exc)}")
    return ToolOutput(content=f"edited {args.path}: replaced {count} occurrence(s)")


def edit_tool() -> Tool[EditArgs]:
    return Tool(
        name="edit",
        description=(
            "Replace exact text in a file. old_string must match the file exactly, including "
            "whitespace, and must be unique unless replace_all is set."
        ),
        args_model=EditArgs,
        side_effect="mutating",
        handler=_edit,
        touches=lambda args: [args.path],
    )
