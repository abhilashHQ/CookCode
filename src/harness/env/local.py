import asyncio
import contextlib
import os
import shutil
import signal
import time
from pathlib import Path

from harness.core.cancel import CancelToken
from harness.core.ids import new_id
from harness.env.base import ExecResult

_READ_CHUNK = 65536
_PUMP_DRAIN_TIMEOUT_S = 5.0


class _CappedBuffer:
    def __init__(self, max_bytes: int) -> None:
        self._half = max(max_bytes // 2, 1)
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._half - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self._tail += chunk
            overflow = len(self._tail) - self._half
            if overflow > 0:
                del self._tail[:overflow]

    @property
    def truncated(self) -> bool:
        return self.total > len(self._head) + len(self._tail)

    def render(self) -> str:
        head = self._head.decode(errors="replace")
        tail = self._tail.decode(errors="replace")
        if not self.truncated:
            return head + tail
        dropped = self.total - len(self._head) - len(self._tail)
        return f"{head}\n[... {dropped} bytes truncated ...]\n{tail}"


def _kill_group(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


class LocalEnvironment:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self._root / candidate

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
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                cmd,
                cwd=self._resolve(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=write_fd,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            pipe_reader.close()
            raise
        finally:
            os.close(write_fd)
        buffer = _CappedBuffer(max_output_bytes)
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
        timed_out = not done
        cancelled = cancel_task in done and wait_task not in done
        _kill_group(proc.pid)
        await wait_task
        cancel_task.cancel()
        try:
            await asyncio.wait_for(pump_task, timeout=_PUMP_DRAIN_TIMEOUT_S)
        except TimeoutError:
            pump_task.cancel()
        transport.close()
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
        target = self._resolve(path)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{new_id()}.tmp")
            tmp.write_bytes(data)
            if target.exists():
                shutil.copymode(target, tmp)
            os.replace(tmp, target)

        await asyncio.to_thread(write)
