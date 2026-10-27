from datetime import datetime


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp from the tracker export."""
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=None)


def parse_row(line: str) -> dict:
    user, start, end = (part.strip() for part in line.split(";"))
    return {"user": user, "start": start, "end": end}
