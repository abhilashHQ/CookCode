from typing import Protocol

from pydantic import BaseModel, ConfigDict

from harness.core.cancel import CancelToken


class ExecResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int | None
    output: str
    output_bytes: int
    duration_s: float
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False


class Environment(Protocol):
    async def exec(
        self,
        cmd: str,
        *,
        cwd: str = ".",
        timeout_s: float,
        max_output_bytes: int,
        cancel: CancelToken,
    ) -> ExecResult: ...

    async def read_file(self, path: str) -> bytes: ...

    async def write_file(self, path: str, data: bytes) -> None: ...
