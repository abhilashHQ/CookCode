import codecs
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from cookcode.core.cancel import CancelToken


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
    @property
    def root(self) -> str: ...

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

    async def remove_file(self, path: str) -> None: ...


def render_capped(head: bytes, tail: bytes, total: int) -> str:
    """Renders kept head and tail bytes, with a notice for the bytes dropped between them."""
    if total <= len(head) + len(tail):
        return (head + tail).decode(errors="replace")
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text = decoder.decode(head)
    partial_head = len(decoder.getstate()[0])
    skip = 0
    while skip < min(3, len(tail)) and tail[skip] & 0xC0 == 0x80:
        skip += 1
    rest = tail[skip:].decode(errors="replace")
    dropped = total - len(head) - len(tail) + partial_head + skip
    return f"{text}\n[... {dropped} bytes truncated ...]\n{rest}"
