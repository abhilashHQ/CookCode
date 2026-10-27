from datetime import UTC

from cookcode.core.ids import new_id, utc_now


def test_new_id_is_unique_hex() -> None:
    ids = {new_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)


def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().tzinfo is UTC
