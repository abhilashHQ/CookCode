import fcntl
import os
import re
from pathlib import Path
from types import TracebackType

from harness.core.events import Event, EventBody, from_json_line, make_event
from harness.core.ids import new_id
from harness.redact import Redactor
from harness.store.base import SessionLockedError, SessionNotFoundError

DURABLE_TYPES = frozenset({"tool_call_started"})
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def _read_events(path: Path) -> list[Event]:
    events: list[Event] = []
    with path.open("rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            events.append(from_json_line(raw.decode()))
    return events


def _truncate_torn_tail(path: Path) -> None:
    with path.open("r+b") as f:
        data = f.read()
        if data and not data.endswith(b"\n"):
            f.truncate(data.rfind(b"\n") + 1)


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
        _truncate_torn_tail(path)
        existing = _read_events(path)
        self._next_seq = existing[-1].seq + 1 if existing else 0
        self._file = path.open("ab")
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
        event = make_event(self._next_seq, body)
        event = Event.model_validate(self._redactor.redact(event.model_dump(mode="json")))
        self._file.write((event.model_dump_json() + "\n").encode())
        self._file.flush()
        if durable or body.type in DURABLE_TYPES:
            os.fsync(self._file.fileno())
        self._next_seq += 1
        return event

    def read(self, from_seq: int = 0) -> list[Event]:
        return [event for event in _read_events(self._path) if event.seq >= from_seq]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._file.close()
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)

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
        lock_path = self._root / f"{session_id}.lock"
        return JsonlSessionLog(session_id, path, lock_path, self._redactor)

    def read(self, session_id: str, from_seq: int = 0) -> list[Event]:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFoundError(session_id)
        return [event for event in _read_events(path) if event.seq >= from_seq]

    def fork(self, session_id: str, at_seq: int, new_session_id: str) -> None:
        prefix = [event for event in self.read(session_id) if event.seq <= at_seq]
        if not prefix or prefix[-1].seq != at_seq:
            raise ValueError(f"seq {at_seq} not found in session {session_id}")
        target = self._path(new_session_id)
        tmp = target.with_name(f"{target.name}.{new_id()}.tmp")
        try:
            with tmp.open("wb") as f:
                for event in prefix:
                    f.write((event.model_dump_json() + "\n").encode())
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, target)
            except FileExistsError:
                raise FileExistsError(new_session_id) from None
            dir_fd = os.open(self._root, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            tmp.unlink(missing_ok=True)

    def list_sessions(self) -> list[str]:
        return sorted(path.stem for path in self._root.glob("*.jsonl"))
