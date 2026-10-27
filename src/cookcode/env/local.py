import asyncio
import contextlib
import os
import shutil
import signal
import time
from asyncio.subprocess import Process
from pathlib import Path

from cookcode.core.cancel import CancelToken
from cookcode.core.ids import new_id
from cookcode.env.base import ExecResult, render_capped

_READ_CHUNK = 65536
_PUMP_DRAIN_TIMEOUT_S = 5.0


class _CappedBuffer:
    def __init__(self, max_bytes: int) -> None:
        self._head_cap = max(max_bytes, 0) // 2
        self._tail_cap = max(max_bytes, 0) - self._head_cap
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._head_cap - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self._tail += chunk
            overflow = len(self._tail) - self._tail_cap
            if overflow > 0:
                del self._tail[:overflow]

    @property
    def truncated(self) -> bool:
        return self.total > len(self._head) + len(self._tail)

    def render(self) -> str:
        return render_capped(bytes(self._head), bytes(self._tail), self.total)


def _kill_group(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


async def _kill_and_reap(proc: Process) -> None:
    _kill_group(proc.pid)
    await proc.wait()


def _fsync_dir(path: Path) -> None:
    dir_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class LocalEnvironment:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> str:
        return str(self._root)

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self._root / candidate

    async def _spawn(self, cmd: str, cwd: str, stdout_fd: int) -> Process:
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                "bash",
                "-c",
                cmd,
                cwd=self._resolve(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout_fd,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        )
        try:
            return await asyncio.shield(spawn)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await _kill_and_reap(await spawn)
            raise

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
            return ExecResult(exit_code=None, output="", output_bytes=0, duration_s=0.0,
                              cancelled=True)
        read_fd, write_fd = os.pipe()
        pipe_reader = os.fdopen(read_fd, "rb", buffering=0)
        try:
            proc = await self._spawn(cmd, cwd, write_fd)
        except BaseException:
            pipe_reader.close()
            raise
        finally:
            os.close(write_fd)
        buffer = _CappedBuffer(max_output_bytes)
        transport: asyncio.ReadTransport | None = None
        pump_task: asyncio.Task[None] | None = None
        cancel_task: asyncio.Task[None] | None = None
        try:
            stdout = asyncio.StreamReader()
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(stdout), pipe_reader
            )

            async def pump() -> None:
                while chunk := await stdout.read(_READ_CHUNK):
                    buffer.feed(chunk)

            pump_task = asyncio.create_task(pump())
            wait_task = asyncio.create_task(proc.wait())
            cancel_task = asyncio.create_task(cancel.wait())
            done, _ = await asyncio.wait(
                {wait_task, cancel_task}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
            try:
                await _kill_and_reap(proc)
                if pump_task is not None:
                    try:
                        await asyncio.wait_for(pump_task, timeout=_PUMP_DRAIN_TIMEOUT_S)
                    except TimeoutError:
                        pump_task.cancel()
            finally:
                if transport is not None:
                    transport.close()
                else:
                    pipe_reader.close()
        timed_out = not done
        cancelled = cancel_task in done and wait_task not in done
        return ExecResult(
            exit_code=None if timed_out or cancelled else proc.returncode,
            output=buffer.render(),
            output_bytes=buffer.total,
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=buffer.truncated,
        )

    async def read_file(self, path: str) -> bytes:
        return await asyncio.to_thread(self._resolve(path).read_bytes)

    async def write_file(self, path: str, data: bytes) -> None:
        target = Path(os.path.realpath(self._resolve(path)))

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.parent / f".{new_id()}.tmp"
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                if target.exists():
                    shutil.copymode(target, tmp)
                os.replace(tmp, target)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            _fsync_dir(target.parent)

        await asyncio.to_thread(write)

    async def remove_file(self, path: str) -> None:
        await asyncio.to_thread(self._resolve(path).unlink, missing_ok=True)
