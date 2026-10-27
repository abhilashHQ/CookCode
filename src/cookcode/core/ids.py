import uuid
from datetime import UTC, datetime


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)
