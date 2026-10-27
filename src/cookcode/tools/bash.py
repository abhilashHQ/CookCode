import secrets
import shlex

from pydantic import BaseModel, Field

from cookcode.agent.tools import Tool, ToolContext, ToolOutput, workspace_path

DESCRIPTION = (
    "Run a shell command with bash. Each call runs in a fresh shell: a directory change made "
    "with cd persists to later calls, but exported variables and shell state do not. Stdout and "
    "stderr are merged, output is capped, and a non-zero exit is reported as an error."
)
CD_FAILED_EXIT = 97


class BashArgs(BaseModel):
    command: str = Field(description="The command to run.")
    timeout_s: float | None = Field(
        default=None, gt=0, description="Timeout in seconds, capped by the tool timeout."
    )


def _wrap(command: str, cwd: str, sentinel: str, no_cwd: str) -> str:
    target = cwd if cwd.startswith("/") else f"./{cwd}"
    enter = (
        f"cd -P -- {shlex.quote(target)} 2>/dev/null || "
        f"{{ printf '%s\\n' {no_cwd}; exit {CD_FAILED_EXIT}; }}"
    )
    trap = f'printf "\\n%s\\n%s\\n" {sentinel} "$(pwd -P 2>/dev/null)"'
    return f"{enter}\ntrap '{trap}' EXIT\n{command}"


def _split_trailer(output: str, sentinel: str) -> tuple[str, str] | None:
    marker = f"\n{sentinel}\n"
    index = output.rfind(marker)
    if index == -1:
        return None
    final = output[index + len(marker) :].split("\n")[0]
    if not final:
        return None
    return output[:index], final


def _reset(cwd: str) -> ToolOutput:
    return ToolOutput(
        content=(
            f"the working directory {cwd} no longer exists or cannot be entered, so the command "
            "did not run. The working directory is now the workspace root."
        ),
        status="error",
        exit_status="error",
        cwd=".",
    )


async def _bash(args: BashArgs, ctx: ToolContext) -> ToolOutput:
    token = secrets.token_hex(16)
    sentinel = f"__flagship_cwd_{token}__"
    no_cwd = f"__flagship_nocwd_{token}__"
    wrapped = _wrap(args.command, ctx.cwd, sentinel, no_cwd)
    timeout_s = ctx.timeout_s if args.timeout_s is None else min(args.timeout_s, ctx.timeout_s)
    try:
        result = await ctx.env.exec(
            wrapped,
            cwd=".",
            timeout_s=timeout_s,
            max_output_bytes=ctx.max_output_bytes,
            cancel=ctx.cancel,
        )
    except Exception as exc:
        return ToolOutput(
            content=f"bash failed to run: {type(exc).__name__}: {exc}",
            status="error",
            exit_status="error",
        )
    if result.exit_code == CD_FAILED_EXIT and result.output.strip() == no_cwd:
        return _reset(ctx.cwd)
    content = result.output
    cwd: str | None = None
    trailer = _split_trailer(content, sentinel)
    if trailer is not None:
        content, final = trailer
        content = content.removesuffix("\n")
        cwd = workspace_path(ctx.env.root, ".", final)
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
