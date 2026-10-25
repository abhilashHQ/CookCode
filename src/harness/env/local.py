import asyncio
import os
import shutil
from pathlib import Path

from harness.core.ids import new_id


class LocalEnvironment:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self._root / candidate

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
