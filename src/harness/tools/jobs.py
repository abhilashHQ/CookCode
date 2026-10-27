"""Background jobs: a command that outlives the bash timeout, started now and checked later.

Everything goes through `Environment.exec`, so jobs work the same on the local machine and in a
container. A job runs in its own session, so the exec that starts it does not kill it on return;
its state lives under JOB_ROOT and its whole process group dies when it ends, is killed or hits
its lifetime cap."""

import re
import secrets
import shlex
from typing import Any

from pydantic import BaseModel, Field

from harness.agent.tools import Tool, ToolContext, ToolOutput

JOB_ROOT = "/tmp/harness-jobs"
JOB_ID = re.compile(r"^j[0-9a-f]{8}$")
MAX_LIFETIME_S = 4 * 3600
WAIT_MARGIN_S = 5.0
TAIL_RESERVE = 200
NO_JOB_EXIT = 3
CD_FAILED_EXIT = 97

START_DESCRIPTION = (
    "Start a long-running shell command in the background and return a job id at once. Use it "
    "for anything that may outlast the bash timeout: builds, test suites, servers, password "
    "crackers, downloads. The job runs in the current directory and keeps running across calls "
    "until it exits, is killed with job_check, or reaches max_s. Check it with job_check."
)
CHECK_DESCRIPTION = (
    "Check a background job: its state (running, exited with a code, or killed) and the tail "
    "of its combined output. wait_s waits up to that many seconds for the job to finish first. "
    "kill=true kills the job and everything it started."
)

_RUNNER = (
    'echo $$ > "$0/pid"\n'
    '( sleep "$2"; echo "lifetime cap of $2s" > "$0/killed"; kill -KILL -- -$$ ) '
    ">/dev/null 2>&1 &\n"
    '( eval "$1" )\n'
    'echo $? > "$0/exit"\n'
    "kill -KILL -- -$$\n"
)


class StartArgs(BaseModel):
    command: str = Field(description="The command to run in the background.")
    max_s: int = Field(
        default=3600, gt=0, le=MAX_LIFETIME_S, description="Kill the job after this many seconds."
    )


class CheckArgs(BaseModel):
    job_id: str = Field(description="The id job_start returned.")
    wait_s: float = Field(
        default=0, ge=0, description="Wait up to this many seconds for the job to finish."
    )
    kill: bool = Field(default=False, description="Kill the job.")


def _dir(job_id: str) -> str:
    return shlex.quote(f"{JOB_ROOT}/{job_id}")


def _error(content: str) -> ToolOutput:
    return ToolOutput(content=content, status="error", exit_status="error")


def _start_script(job_id: str, command: str, cwd: str, max_s: int) -> str:
    d = _dir(job_id)
    target = cwd if cwd.startswith("/") else f"./{cwd}"
    return (
        f"cd -P -- {shlex.quote(target)} 2>/dev/null || exit {CD_FAILED_EXIT}\n"
        f"mkdir -p {d}\n"
        f"setsid bash -c {shlex.quote(_RUNNER)} {d} {shlex.quote(command)} {max_s}"
        f" < /dev/null > {d}/out 2>&1 &\n"
        f"for _ in $(seq 50); do [ -s {d}/pid ] && break; sleep 0.1; done\n"
        f"[ -s {d}/pid ]"
    )


def _check_script(job_id: str, wait_s: float, kill: bool, tail_bytes: int) -> str:
    d = _dir(job_id)
    stop = (
        f'if [ ! -e {d}/exit ] && [ ! -e {d}/killed ]; then echo "by job_check" > {d}/killed; '
        f'kill -KILL -- "-$(cat {d}/pid)" 2>/dev/null; fi\n'
        if kill
        else ""
    )
    return (
        f"[ -s {d}/pid ] || exit {NO_JOB_EXIT}\n"
        f"end=$((SECONDS + {int(wait_s)}))\n"
        f"while [ ! -e {d}/exit ] && [ ! -e {d}/killed ] && [ $SECONDS -lt $end ]; do"
        " sleep 1; done\n"
        f"{stop}"
        f'if [ -e {d}/exit ]; then state="exited with code $(cat {d}/exit)"\n'
        f'elif [ -e {d}/killed ]; then state="killed ($(cat {d}/killed))"\n'
        f'elif kill -0 -- "-$(cat {d}/pid)" 2>/dev/null; then state=running\n'
        "else state=\"gone without an exit code\"; fi\n"
        f"size=$(wc -c < {d}/out)\n"
        f'echo "[job {job_id}: $state, $size bytes of output]"\n'
        f'if [ "$size" -gt {tail_bytes} ]; then echo "[... last {tail_bytes} bytes ...]"; fi\n'
        f"tail -c {tail_bytes} {d}/out"
    )


async def _start(args: StartArgs, ctx: ToolContext) -> ToolOutput:
    job_id = f"j{secrets.token_hex(4)}"
    script = _start_script(job_id, args.command, ctx.cwd, args.max_s)
    try:
        result = await ctx.env.exec(
            script, cwd=".", timeout_s=ctx.timeout_s, max_output_bytes=ctx.max_output_bytes,
            cancel=ctx.cancel,
        )
    except Exception as exc:
        return _error(f"job_start failed to run: {type(exc).__name__}: {exc}")
    if result.exit_code == CD_FAILED_EXIT:
        return _error(f"the working directory {ctx.cwd} no longer exists, so the job did not start")
    if result.exit_code != 0:
        return _error(f"the job did not start: {result.output.strip() or result.exit_code}")
    return ToolOutput(
        content=f'started job {job_id}. Check it with job_check(job_id="{job_id}").'
    )


async def _check(args: CheckArgs, ctx: ToolContext) -> ToolOutput:
    if not JOB_ID.match(args.job_id):
        return _error(f"{args.job_id!r} is not a job id; job_start returns ids like j1a2b3c4d")
    wait_s = min(args.wait_s, max(ctx.timeout_s - WAIT_MARGIN_S, 0))
    tail_bytes = max(ctx.max_output_bytes - TAIL_RESERVE, 1)
    script = _check_script(args.job_id, wait_s, args.kill, tail_bytes)
    try:
        result = await ctx.env.exec(
            script, cwd=".", timeout_s=ctx.timeout_s, max_output_bytes=ctx.max_output_bytes,
            cancel=ctx.cancel,
        )
    except Exception as exc:
        return _error(f"job_check failed to run: {type(exc).__name__}: {exc}")
    if result.exit_code == NO_JOB_EXIT:
        return _error(f"no job {args.job_id}")
    if result.exit_code != 0:
        return _error(result.output or f"job_check failed with exit code {result.exit_code}")
    return ToolOutput(content=result.output, output_bytes=result.output_bytes,
                      truncated=result.truncated)


def job_tools() -> list[Tool[Any]]:
    start: Tool[StartArgs] = Tool(
        name="job_start", description=START_DESCRIPTION, args_model=StartArgs,
        side_effect="mutating", handler=_start,
    )
    check: Tool[CheckArgs] = Tool(
        name="job_check", description=CHECK_DESCRIPTION, args_model=CheckArgs,
        side_effect="mutating", handler=_check,
    )
    return [start, check]
