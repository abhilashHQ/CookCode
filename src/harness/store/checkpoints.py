import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from harness.core.ids import new_id

_CHECKPOINT_ID = re.compile(r"[A-Za-z0-9_-]+")


def _atomic_write(target: Path, data: bytes) -> None:
    tmp = target.with_name(f".{target.name}.{new_id()}.tmp")
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class FileCheckpointStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._blobs = root / "blobs"

    def _manifest(self, checkpoint_id: str) -> Path:
        if not _CHECKPOINT_ID.fullmatch(checkpoint_id):
            raise ValueError(f"invalid checkpoint id {checkpoint_id!r}")
        return self._root / f"{checkpoint_id}.json"

    def save(self, checkpoint_id: str, files: Mapping[str, bytes | None]) -> None:
        manifest_path = self._manifest(checkpoint_id)
        self._blobs.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, str | None] = {}
        wrote_blob = False
        for path, data in files.items():
            if data is None:
                manifest[path] = None
                continue
            digest = hashlib.sha256(data).hexdigest()
            blob = self._blobs / digest
            if not blob.exists():
                _atomic_write(blob, data)
                wrote_blob = True
            manifest[path] = digest
        if wrote_blob:
            _fsync_dir(self._blobs)
        _atomic_write(manifest_path, json.dumps(manifest, sort_keys=True).encode())
        _fsync_dir(self._root)

    def load(self, checkpoint_id: str) -> dict[str, bytes | None]:
        manifest: dict[str, str | None] = json.loads(self._manifest(checkpoint_id).read_bytes())
        files: dict[str, bytes | None] = {}
        for path, digest in manifest.items():
            if digest is None:
                files[path] = None
                continue
            data = (self._blobs / digest).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"checkpoint {checkpoint_id}: blob for {path} is corrupt")
            files[path] = data
        return files
