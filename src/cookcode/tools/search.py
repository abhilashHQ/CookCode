import posixpath
import shlex

from pydantic import BaseModel, Field

from cookcode.agent.tools import Tool, ToolContext, ToolOutput
from cookcode.env.base import ExecResult

MAX_GLOB_RESULTS = 500
NO_FILES = "no files matched"
NO_MATCHES = "no matches"
CD_FAILED = 3
_REGEX_SPECIAL = frozenset(".[](){}*+?|^$\\")


def _error(message: str) -> ToolOutput:
    return ToolOutput(content=message, status="error", exit_status="error")


async def _exec(ctx: ToolContext, cmd: str) -> ExecResult | ToolOutput:
    try:
        result = await ctx.env.exec(
            cmd,
            cwd=ctx.cwd,
            timeout_s=ctx.timeout_s,
            max_output_bytes=ctx.max_output_bytes,
            cancel=ctx.cancel,
        )
    except Exception as exc:
        return _error(f"search failed: {type(exc).__name__}: {exc}")
    if result.cancelled:
        return ToolOutput(
            content=result.output,
            status="error",
            exit_status="killed",
            output_bytes=result.output_bytes,
            truncated=result.truncated,
        )
    if result.timed_out:
        note = f"[timed out after {ctx.timeout_s:g}s]"
        return ToolOutput(
            content=f"{result.output}\n{note}" if result.output else note,
            status="error",
            exit_status="timeout",
            output_bytes=result.output_bytes,
            truncated=result.truncated,
        )
    return result


def _strip_dot_slash(line: str) -> str:
    return line[2:] if line.startswith("./") else line


class GrepArgs(BaseModel):
    pattern: str = Field(description="Regular expression to search for.")
    path: str = Field(default=".", description="File or directory to search.")
    glob: str | None = Field(default=None, description="Only search files matching this glob.")
    ignore_case: bool = Field(default=False, description="Match case-insensitively.")
    max_results: int = Field(default=200, ge=1, description="Maximum number of matching lines.")


def _grep_command(args: GrepArgs, prefer_rg: bool) -> str:
    quote = shlex.quote
    # rg searches in parallel, so its output order changes from run to run unless sorted.
    rg = ["rg", "-n", "-H", "--no-heading", "--color", "never", "--no-config", "--sort=path"]
    grep = ["grep", "-rnHIE", quote("--exclude-dir=.[!.]*")]
    if args.ignore_case:
        rg.append("-i")
        grep.append("-i")
    if args.glob is not None:
        rg.append(quote(f"--glob={args.glob}"))
        grep.append(quote(f"--include={args.glob}"))
    # A glob overrides rg's hidden-file skip and the last matching glob wins, so hidden
    # directories are excluded after it, as the fallback's --exclude-dir does.
    rg.append(quote("--glob=!.*/"))
    tail = ["-e", quote(args.pattern), "--", quote(args.path)]
    grep_command = " ".join([*grep, *tail])
    if not prefer_rg:
        return grep_command
    rg_command = " ".join([*rg, *tail])
    return f"if command -v rg >/dev/null 2>&1; then {rg_command}; else {grep_command}; fi"


def grep_tool(*, prefer_rg: bool = True) -> Tool[GrepArgs]:
    async def handler(args: GrepArgs, ctx: ToolContext) -> ToolOutput:
        result = await _exec(ctx, _grep_command(args, prefer_rg))
        if isinstance(result, ToolOutput):
            return result
        if result.exit_code == 1:
            return ToolOutput(content=NO_MATCHES)
        if result.exit_code != 0:
            return ToolOutput(
                content=result.output or f"search failed with exit code {result.exit_code}",
                status="error",
                exit_status="error",
                output_bytes=result.output_bytes,
                truncated=result.truncated,
            )
        lines = [_strip_dot_slash(line) for line in result.output.splitlines()]
        if len(lines) > args.max_results:
            extra = len(lines) - args.max_results
            lines = [*lines[: args.max_results], f"[... {extra} more matches]"]
        return ToolOutput(
            content="\n".join(lines) if lines else NO_MATCHES,
            output_bytes=result.output_bytes,
            truncated=result.truncated,
        )

    return Tool(
        name="grep",
        description=(
            "Search file contents with a regular expression. Returns path:line:text lines. "
            "Use glob to restrict the files searched."
        ),
        args_model=GrepArgs,
        side_effect="read_only",
        handler=handler,
    )


class GlobArgs(BaseModel):
    pattern: str = Field(description="Glob such as src/**/*.py. Without a slash it matches names.")
    path: str = Field(default=".", description="Directory to search in.")


def _escape(char: str) -> str:
    return f"\\{char}" if char in _REGEX_SPECIAL else char


def _class_end(glob: str, start: int) -> int:
    index = start + 1
    if index < len(glob) and glob[index] in "!^":
        index += 1
    if index < len(glob) and glob[index] == "]":
        index += 1
    return glob.find("]", index)


def _brace_end(glob: str, start: int) -> int:
    depth = 0
    for index in range(start, len(glob)):
        if glob[index] == "{":
            depth += 1
        elif glob[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_alternatives(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def glob_to_regex(glob: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(glob):
        char = glob[index]
        if glob.startswith("**/", index):
            out.append("(.*/)?")
            index += 3
        elif glob.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        elif char == "[" and (end := _class_end(glob, index)) != -1:
            body = glob[index + 1 : end]
            if body[:1] == "!":
                body = "^" + body[1:]
            out.append(f"[{body}]")
            index = end + 1
        elif char == "{" and (end := _brace_end(glob, index)) != -1:
            alternatives = _split_alternatives(glob[index + 1 : end])
            out.append("(" + "|".join(glob_to_regex(alt) for alt in alternatives) + ")")
            index = end + 1
        else:
            out.append(_escape(char))
            index += 1
    return "".join(out)


def _find_regex(pattern: str) -> str:
    anchored = pattern.startswith("/")
    glob = pattern.lstrip("/")
    if not anchored and "/" not in glob.rstrip("/"):
        glob = "**/" + glob
    return "\\./" + glob_to_regex(glob)


def _explicit(path: str) -> str:
    return path if path.startswith("/") else f"./{path}"


def _glob_command(args: GlobArgs, prefer_rg: bool) -> str:
    quote = shlex.quote
    enter = (
        f"cd -- {quote(_explicit(args.path))} 2>/dev/null || "
        f"{{ echo {quote('directory not found: ' + args.path)}; exit {CD_FAILED}; }}"
    )
    find = (
        "find . -regextype posix-extended -path '*/.*' -prune -o "
        f"-type f -regex {quote(_find_regex(args.pattern))} -print 2>/dev/null"
    )
    rg = f"rg --files --no-config {quote('--glob=' + args.pattern)} . 2>/dev/null"
    listing = f"if command -v rg >/dev/null 2>&1; then {rg}; else {find}; fi" if prefer_rg else find
    bound = f"LC_ALL=C sort | awk 'NR <= {MAX_GLOB_RESULTS} {{ print }} END {{ print NR }}'"
    return f"{enter}; set -o pipefail; {{ {listing}; }} | {bound}"


def _prefix(path: str) -> str:
    normalized = posixpath.normpath(path)
    return "" if normalized == "." else normalized.rstrip("/") + "/"


def glob_tool(*, prefer_rg: bool = True) -> Tool[GlobArgs]:
    async def handler(args: GlobArgs, ctx: ToolContext) -> ToolOutput:
        result = await _exec(ctx, _glob_command(args, prefer_rg))
        if isinstance(result, ToolOutput):
            return result
        if result.exit_code == CD_FAILED:
            return _error(result.output.strip())
        lines = result.output.splitlines()
        total = int(lines[-1]) if lines and lines[-1].isdigit() else None
        if result.truncated:
            matched = "" if total is None else f" ({total} files matched)"
            return _error(
                f"glob output exceeded the {ctx.max_output_bytes}-byte output cap{matched}; "
                "narrow the pattern or the path"
            )
        if total is None:
            return _error(f"glob failed with exit code {result.exit_code}")
        if total == 0:
            if result.exit_code in (0, 1):
                return ToolOutput(content=NO_FILES)
            return _error(f"glob failed with exit code {result.exit_code}")
        prefix = _prefix(args.path)
        files = [prefix + _strip_dot_slash(line) for line in lines[:-1] if line]
        if total > MAX_GLOB_RESULTS:
            files.append(f"[... {total - MAX_GLOB_RESULTS} more]")
        return ToolOutput(content="\n".join(files), output_bytes=result.output_bytes)

    return Tool(
        name="glob",
        description=(
            "List files matching a glob pattern, sorted. A pattern without a slash matches file "
            "names at any depth; use src/**/*.py to match a path. Hidden directories are skipped."
        ),
        args_model=GlobArgs,
        side_effect="read_only",
        handler=handler,
    )
