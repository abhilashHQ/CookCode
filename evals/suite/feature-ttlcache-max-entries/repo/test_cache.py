import pytest

from cache import TTLCache


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_get_and_expire():
    clock = FakeClock()
    cache = TTLCache(10, clock=clock)
    cache.set("a", 1)
    assert cache.get("a") == 1
    clock.now = 10
    assert cache.get("a") is None


def test_contains_and_len():
    clock = FakeClock()
    cache = TTLCache(5, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    assert "a" in cache and len(cache) == 2
    clock.now = 6
    assert "a" not in cache and len(cache) == 0


def test_rejects_bad_ttl():
    with pytest.raises(ValueError):
        TTLCache(0)
