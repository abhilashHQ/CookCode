from typing import Protocol

from harness.core.events import Event, EventBody


class SessionLockedError(Exception):
    pass


class SessionNotFoundError(Exception):
    pass


class SessionCorruptError(Exception):
    pass


class SessionLog(Protocol):
    @property
    def session_id(self) -> str: ...

    @property
    def next_seq(self) -> int: ...

    def append(self, body: EventBody, *, durable: bool = False) -> Event: ...

    def read(self, from_seq: int = 0) -> list[Event]: ...

    def close(self) -> None: ...


class SessionStore(Protocol):
    def open(self, session_id: str, *, create: bool = False) -> SessionLog: ...

    def read(self, session_id: str, from_seq: int = 0) -> list[Event]: ...

    def fork(self, session_id: str, at_seq: int, new_session_id: str) -> None: ...

    def list_sessions(self) -> list[str]: ...
