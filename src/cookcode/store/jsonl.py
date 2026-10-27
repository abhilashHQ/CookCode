import fcntl
import os
import re
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

from cookcode.core.events import Event, EventBody, from_json_line, make_event
from cookcode.core.ids import new_id
from cookcode.redact import Redactor
from cookcode.store.base import SessionCorruptError, SessionLockedError, SessionNotFoundError

DURABLE_TYPES = frozenset({"tool_call_started"})
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def _complete_lines(path: Path) -> Iterator[tuple[bytes, Event]]:
    with path.open("rb") as f:
        for lineno, raw in enumerate(f, start=1):
            if not raw.endswith(b"\n"):
                return
            try:
                event = from_json_line(raw.decode())
            except ValueError as exc:
                raise SessionCorruptError(f"{path}: line {lineno}: {exc}") from exc
            yield raw, event


def _read_events(path: Path) -> list[Event]:
    return [event for _, event in _complete_lines(path)]


def _truncate_torn_tail(path: Path) -> None:
    with path.open("r+b") as f:
        data = f.read()
        if data and not data.endswith(b"\n"):
            f.truncate(data.rfind(b"\n") + 1)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _fsync_dir(path: Path) -> None:
    dir_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class JsonlSessionLog:
    def __init__(self, session_id: str, path: Path, lock_path: Path, redactor: Redactor) -> None:
        self._session_id = session_id
        self._path = path
        self._redactor = redactor
        self._lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            raise SessionLockedError(session_id) from None
        try:
            _truncate_torn_tail(path)
            existing = _read_events(path)
            self._fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        except BaseException:
            self._release_lock()
            raise
        self._next_seq = existing[-1].seq + 1 if existing else 0
        self._end = os.fstat(self._fd).st_size
        self._poisoned = False
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def append(self, body: EventBody, *, durable: bool = False) -> Event:
        if self._closed:
            raise ValueError(f"session log {self._session_id} is closed")
        if self._poisoned:
            raise ValueError(f"session log {self._session_id} failed a rollback, reopen it")
        event = make_event(self._next_seq, body)
        event = Event.model_validate(self._redactor.redact(event.model_dump(mode="json")))
        line = (event.model_dump_json() + "\n").encode()
        try:
            _write_all(self._fd, line)
            if durable or body.type in DURABLE_TYPES:
                os.fsync(self._fd)
        except BaseException:
            try:
                os.ftruncate(self._fd, self._end)
            except OSError:
                self._poisoned = True
            raise
        self._end += len(line)
        self._next_seq += 1
        return event

    def read(self, from_seq: int = 0) -> list[Event]:
        return [event for event in _read_events(self._path) if event.seq >= from_seq]

    def _release_lock(self) -> None:
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._fd)
        finally:
            self._release_lock()

    def __enter__(self) -> "JsonlSessionLog":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class JsonlSessionStore:
    def __init__(self, root: Path, redactor: Redactor | None = None) -> None:
        self._root = root
        self._redactor = redactor or Redactor()
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError(f"invalid session id {session_id!r}")
        return self._root / f"{session_id}.jsonl"

    def open(self, session_id: str, *, create: bool = False) -> JsonlSessionLog:
        path = self._path(session_id)
        if not path.exists():
            if not create:
                raise SessionNotFoundError(session_id)
            path.touch()
            _fsync_dir(self._root)
        lock_path = self._root / f"{session_id}.lock"
        return JsonlSessionLog(session_id, path, lock_path, self._redactor)

    def read(self, session_id: str, from_seq: int = 0) -> list[Event]:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFoundError(session_id)
        return [event for event in _read_events(path) if event.seq >= from_seq]

    def fork(self, session_id: str, at_seq: int, new_session_id: str) -> None:
        source = self._path(session_id)
        if not source.exists():
            raise SessionNotFoundError(session_id)
        prefix: list[bytes] = []
        found = False
        for raw, event in _complete_lines(source):
            if event.seq > at_seq:
                break
            prefix.append(raw)
            if event.seq == at_seq:
                found = True
                break
        if not found:
            raise ValueError(f"seq {at_seq} not found in session {session_id}")
        target = self._path(new_session_id)
        tmp = target.with_name(f"{target.name}.{new_id()}.tmp")
        try:
            with tmp.open("wb") as f:
                f.writelines(prefix)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, target)
            except FileExistsError:
                raise FileExistsError(new_session_id) from None
            _fsync_dir(self._root)
        finally:
            tmp.unlink(missing_ok=True)

    def list_sessions(self) -> list[str]:
        return sorted(path.stem for path in self._root.glob("*.jsonl"))
