import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import pytest
from cache import TTLCache

class FakeClock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now

def make(ttl=10, max_entries=2):
    clock = FakeClock()
    return TTLCache(ttl, clock=clock, max_entries=max_entries), clock

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
    cache.set("a", 1); cache.set("b", 2)
    assert "a" in cache and len(cache) == 2
    clock.now = 6
    assert "a" not in cache and len(cache) == 0

def test_rejects_bad_ttl():
    with pytest.raises(ValueError):
        TTLCache(0)

@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_bad_max_entries(bad):
    with pytest.raises(ValueError):
        TTLCache(10, max_entries=bad)

def test_unlimited_by_default():
    cache = TTLCache(10, clock=FakeClock())
    for i in range(500):
        cache.set(i, i)
    assert len(cache) == 500

def test_evicts_oldest_insert():
    cache, _ = make()
    cache.set("a", 1); cache.set("b", 2); cache.set("c", 3)
    assert cache.get("a") is None and cache.get("b") == 2 and cache.get("c") == 3

def test_get_counts_as_use():
    cache, _ = make()
    cache.set("a", 1); cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1 and cache.get("c") == 3

def test_overwrite_counts_as_use_and_does_not_evict():
    cache, _ = make()
    cache.set("a", 1); cache.set("b", 2)
    cache.set("a", 10)
    assert len(cache) == 2 and cache.get("b") == 2
    cache, _ = make()
    cache.set("a", 1); cache.set("b", 2)
    cache.set("a", 10)
    cache.set("c", 3)
    assert cache.get("b") is None and cache.get("a") == 10

def test_contains_does_not_count_as_use():
    cache, _ = make()
    cache.set("a", 1); cache.set("b", 2)
    assert "a" in cache
    cache.set("c", 3)
    assert "a" not in cache
    assert cache.get("b") == 2

def test_missed_get_does_not_disturb_order():
    cache, _ = make()
    cache.set("a", 1); cache.set("b", 2)
    assert cache.get("zzz") is None
    cache.set("c", 3)
    assert cache.get("a") is None and cache.get("b") == 2

def test_expired_entries_dropped_before_lru():
    cache, clock = make(ttl=10, max_entries=2)
    cache.set("a", 1)
    clock.now = 1
    cache.set("b", 2)
    clock.now = 2
    assert cache.get("a") == 1
    clock.now = 10.5
    cache.set("c", 3)
    assert cache.get("b") == 2 and cache.get("c") == 3
    assert len(cache) == 2

def test_len_never_exceeds_limit():
    cache, clock = make(ttl=100, max_entries=3)
    for i in range(50):
        clock.now = i * 0.5
        cache.set(i % 7, i)
        if i % 3 == 0:
            cache.get((i + 2) % 7)
        assert len(cache) <= 3
'''


def fail(msg):
    print(msg)
    sys.exit(1)


def run_hidden_tests(source):
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_hidden.py"
        test_file.write_text(source)
        env = dict(os.environ, PYTHONPATH=str(WORKDIR), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=180,
        )
    if proc.returncode != 0:
        lines = proc.stdout.splitlines()
        summary = [l for l in lines if l.startswith(("FAILED", "ERROR"))] or (proc.stdout + proc.stderr).strip().splitlines() or ["no output"]
        fail("hidden tests failed: " + summary[0][:300])


def require_tests(relpath, names):
    path = WORKDIR / relpath
    if not path.is_file():
        fail(f"{relpath} was deleted")
    text = path.read_text()
    missing = [n for n in names if f"def {n}(" not in text]
    if missing:
        fail(f"{relpath} lost tests: {', '.join(missing)}")


require_tests("test_cache.py", ["test_get_and_expire", "test_contains_and_len", "test_rejects_bad_ttl"])
run_hidden_tests(HIDDEN)
sys.exit(0)
