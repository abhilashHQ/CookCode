"""Environment over a Harbor container: our exec semantics rebuilt on top of its plain exec."""

import asyncio
import base64
import contextlib
import math
import posixpath
import shlex
import tempfile
import time
from pathlib import Path
from typing import Protocol

from harness.core.cancel import CancelToken
from harness.core.ids import new_id
from harness.env.base import ExecResult, render_capped

HEADER = "HARNESS-EXEC"
KILL_GRACE_S = 10.0
BACKSTOP_S = 60
MISSING, DIRECTORY = 2, 21


class ContainerResult(Protocol):
    @property
    def stdout(self) -> str | None: ...

    @property
    def return_code(self) -> int: ...


class Container(Protocol):
    """The part of harbor's BaseEnvironment this environment uses."""

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ContainerResult: ...

    async def upload_file(self, source_path: Path | str, target_path: str) -> None: ...


class ContainerError(OSError):
    pass


def _wrap(cmd: str, cwd: str, work: str, max_output_bytes: int) -> str:
    """The command runs in its own session with its pid on file, so a later exec can kill the
    whole group. Output goes to a file and only a capped, base64-encoded slice comes back."""
    head = max(max_output_bytes, 0) // 2
    tail = max(max_output_bytes, 0) - head
    inner = 'echo $$ > "$0/pid"; cd -- "$1" || exit 1; eval "$2"'
    w = shlex.quote(work)
    return (
        f"mkdir -p {w}\n"
        f"setsid bash -c {shlex.quote(inner)} {w} {shlex.quote(cwd)} {shlex.quote(cmd)}"
        f" < /dev/null > {w}/out 2>&1\n"
        "code=$?\n"
        f'kill -KILL -- "-$(cat {w}/pid)" 2>/dev/null\n'
        f"size=$(wc -c < {w}/out)\n"
        f'echo "{HEADER} $code $size"\n'
        f'if [ "$size" -le {max_output_bytes} ]; then base64 -w0 < {w}/out; echo;'
        f" else head -c {head} {w}/out | base64 -w0; echo;"
        f" tail -c {tail} {w}/out | base64 -w0; echo; fi\n"
        f"rm -rf {w}\n"
    )


def _kill_script(work: str) -> str:
    pid = shlex.quote(f"{work}/pid")
    return (
        f"for _ in $(seq 50); do [ -s {pid} ] && break; sleep 0.1; done\n"
        f'[ -s {pid} ] && kill -KILL -- "-$(cat {pid})" 2>/dev/null\n'
        "true\n"
    )


def _parse(stdout: str) -> tuple[int, str, int, bool]:
    lines = stdout.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith(HEADER + " "))
    except StopIteration:
        raise ContainerError(f"exec wrapper produced no header: {stdout[-500:]!r}") from None
    _, code, size = lines[start].split()
    parts = [base64.b64decode(line) for line in lines[start + 1 : start + 3]]
    head = parts[0] if parts else b""
    tail = parts[1] if len(parts) > 1 else b""
    total = int(size)
    return int(code), render_capped(head, tail, total), total, total > len(head) + len(tail)


class HarborEnvironment:
    def __init__(self, container: Container, root: str) -> None:
        self._container = container
        self._root = posixpath.normpath(root)

    @property
    def root(self) -> str:
        return self._root

    def _resolve(self, path: str) -> str:
        return posixpath.normpath(posixpath.join(self._root, path))

    async def _run(self, script: str, timeout_s: float = 120) -> ContainerResult:
        return await self._container.exec(script, timeout_sec=math.ceil(timeout_s))

    async def exec(
        self,
        cmd: str,
        *,
        cwd: str = ".",
        timeout_s: float,
        max_output_bytes: int,
        cancel: CancelToken,
    ) -> ExecResult:
        started = time.monotonic()
        if cancel.cancelled:
            return ExecResult(
                exit_code=None, output="", output_bytes=0, duration_s=0.0, cancelled=True
            )
        work = f"/tmp/harness-exec-{new_id()}"
        script = _wrap(cmd, self._resolve(cwd), work, max_output_bytes)
        running = asyncio.ensure_future(self._run(script, timeout_s + BACKSTOP_S))
        stopping = asyncio.ensure_future(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {running, stopping}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
            stopped = running not in done
            if stopped:
                await self._kill(work)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(running), KILL_GRACE_S)
        finally:
            stopping.cancel()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, stopping, return_exceptions=True)
        cancelled = stopped and stopping in done
        timed_out = stopped and not cancelled
        if running.cancelled() or running.exception() is not None:
            if not stopped:
                raise running.exception() or ContainerError("exec failed")
            output, total, truncated, code = "", 0, False, None
        else:
            code, output, total, truncated = _parse(running.result().stdout or "")
        return ExecResult(
            exit_code=None if stopped else code,
            output=output,
            output_bytes=total,
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=truncated,
        )

    async def _kill(self, work: str) -> None:
        with contextlib.suppress(Exception):
            await self._run(_kill_script(work), timeout_s=30)

    async def read_file(self, path: str) -> bytes:
        target = shlex.quote(self._resolve(path))
        result = await self._run(
            f"if [ -d {target} ]; then exit {DIRECTORY}; fi\n"
            f"[ -e {target} ] || exit {MISSING}\n"
            f"base64 -w0 < {target}"
        )
        if result.return_code == MISSING:
            raise FileNotFoundError(path)
        if result.return_code == DIRECTORY:
            raise IsADirectoryError(path)
        if result.return_code != 0:
            raise ContainerError(f"cannot read {path}: {(result.stdout or '').strip()}")
        return base64.b64decode((result.stdout or "").strip())

    async def write_file(self, path: str, data: bytes) -> None:
        target = shlex.quote(self._resolve(path))
        resolved = await self._run(
            f't=$(realpath -m {target}) && mkdir -p "${{t%/*}}" && echo "$t"'
        )
        if resolved.return_code != 0:
            raise ContainerError(f"cannot write {path}: {(resolved.stdout or '').strip()}")
        real = (resolved.stdout or "").strip().splitlines()[-1]
        tmp = posixpath.join(posixpath.dirname(real), f".{new_id()}.tmp")
        with tempfile.TemporaryDirectory() as local:
            source = Path(local) / "data"
            source.write_bytes(data)
            await self._container.upload_file(source, tmp)
        r, t = shlex.quote(real), shlex.quote(tmp)
        moved = await self._run(
            f"if [ -e {r} ]; then chmod --reference={r} {t}; chown --reference={r} {t}; "
            f'else chmod 0644 {t}; chown "$(id -u):$(id -g)" {t}; fi 2>/dev/null\n'
            f"mv -f {t} {r} || {{ rm -f {t}; exit 1; }}"
        )
        if moved.return_code != 0:
            raise ContainerError(f"cannot write {path}: {(moved.stdout or '').strip()}")

    async def remove_file(self, path: str) -> None:
        result = await self._run(f"rm -f -- {shlex.quote(self._resolve(path))}")
        if result.return_code != 0:
            raise ContainerError(f"cannot remove {path}: {(result.stdout or '').strip()}")
