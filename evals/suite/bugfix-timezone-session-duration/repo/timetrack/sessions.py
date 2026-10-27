from dataclasses import dataclass
from datetime import datetime

from .parsing import parse_timestamp


@dataclass
class Session:
    user: str
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def load_sessions(rows):
    sessions = []
    for row in rows:
        start = parse_timestamp(row["start"])
        end = parse_timestamp(row["end"])
        if end < start:
            raise ValueError(f"session for {row['user']} ends before it starts")
        sessions.append(Session(row["user"], start, end))
    return sessions


def total_minutes(sessions, user=None):
    return sum(s.minutes for s in sessions if user is None or s.user == user)
