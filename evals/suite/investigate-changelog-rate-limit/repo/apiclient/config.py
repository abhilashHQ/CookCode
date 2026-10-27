import os

FREE_TIER_RPM = 60
DEFAULT_RPM = FREE_TIER_RPM * 2
POOL_SIZE = 120


def rate_limit():
    raw = os.environ.get("RATE_LIMIT")
    return int(raw) if raw else DEFAULT_RPM
