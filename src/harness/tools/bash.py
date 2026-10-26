import posixpath
import secrets

from pydantic import BaseModel, Field

from harness.agent.tools import Tool, ToolContext, ToolOutput

DESCRIPTION = (
    "Run a shell command with bash. Each call runs in a fresh shell: a directory change made "
    "with cd persists to later calls, but exported variables and shell state do not. Stdout and "
    "stderr are merged, output is capped, and a non-zero exit is reported as an error."
)


class BashArgs(BaseModel):
    command: str = Field(description="The command to run.")
    timeout_s: float | None = Field(
        default=None, gt=0, description="Timeout in seconds, capped by the tool timeout."
    )


def _wrap(command: str, sentinel: str, start_var: str) -> str:
    trap = f'printf "\\n%s\\n%s\\n%s\\n" {sentinel} "${start_var}" "$PWD"'
    return f"{start_var}=$PWD; trap '{trap}' EXIT\n{command}"


def _split_trailer(output: str, sentinel: str) -> tuple[str, str, str] | None:
    marker = f"\n{sentinel}\n"
    index = output.rfind(marker)
    if index == -1:
        return None
    lines = output[index + len(marker) :].split("\n")
    if len(lines) < 2 or not lines[0] or not lines[1]:
        return None
    return output[:index], lines[0], lines[1]


def _root_of(start: str, cwd: str) -> str | None:
    tracked = posixpath.normpath(cwd)
    if tracked == ".":
        return start
    if posixpath.isabs(tracked):
        return None
    suffix = "/" + tracked
    if start.endswith(suffix):
        return start[: -len(suffix)] or "/"
    return None


def _report_cwd(start: str, final: str, tracked: str) -> str:
    root = _root_of(start, tracked)
    if root is None:
        return final
    prefix = root.rstrip("/") + "/"
    if final == root or final.startswith(prefix):
        return posixpath.relpath(final, root)
    return final


async def _bash(args: BashArgs, ctx: ToolContext) -> ToolOutput:
    token = secrets.token_hex(16)
    sentinel = f"__flagship_cwd_{token}__"
    wrapped = _wrap(args.command, sentinel, f"__flagship_start_{token}")
    timeout_s = ctx.timeout_s if args.timeout_s is None else min(args.timeout_s, ctx.timeout_s)
    try:
        result = await ctx.env.exec(
            wrapped,
            cwd=ctx.cwd,
            timeout_s=timeout_s,
            max_output_bytes=ctx.max_output_bytes,
            cancel=ctx.cancel,
        )
    except Exception as exc:
        if isinstance(exc, FileNotFoundError | NotADirectoryError) and (
            posixpath.normpath(ctx.cwd) != "."
        ):
            return ToolOutput(
                content=(
                    f"the working directory {ctx.cwd} no longer exists, so the command did not "
                    "run. The working directory is now the workspace root."
                ),
                status="error",
                exit_status="error",
                cwd=".",
            )
        return ToolOutput(
            content=f"bash failed to run: {type(exc).__name__}: {exc}",
            status="error",
            exit_status="error",
        )
    content = result.output
    cwd: str | None = None
    trailer = _split_trailer(content, sentinel)
    if trailer is not None:
        content, start, final = trailer
        content = content.removesuffix("\n")
        cwd = _report_cwd(start, final, ctx.cwd)
    if result.cancelled:
        exit_status = "killed"
    elif result.timed_out:
        exit_status = "timeout"
    elif result.exit_code == 0:
        exit_status = "ok"
    else:
        exit_status = "error"
        note = f"[exit code {result.exit_code}]"
        content = f"{content}\n{note}" if content else note
    return ToolOutput(
        content=content,
        status="ok" if exit_status == "ok" else "error",
        exit_status=exit_status,
        output_bytes=result.output_bytes,
        truncated=result.truncated,
        cwd=cwd,
    )


def bash_tool() -> Tool[BashArgs]:
    return Tool(
        name="bash",
        description=DESCRIPTION,
        args_model=BashArgs,
        side_effect="mutating",
        handler=_bash,
    )
