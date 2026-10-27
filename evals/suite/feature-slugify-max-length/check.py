import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import pytest
from textutils import excerpt, reading_time, slugify

def test_slugify_basic():
    assert slugify("Hello, World!") == "hello-world"

def test_slugify_unicode():
    assert slugify("Crème brûlée recipes") == "creme-brulee-recipes"

def test_slugify_separator():
    assert slugify("a b  c", separator="_") == "a_b_c"

def test_excerpt():
    assert excerpt("one two three four", limit=12) == "one two..."

def test_reading_time():
    assert reading_time("word " * 460) == 2

@pytest.mark.parametrize("limit, expected", [
    (None, "hello-world-again"),
    (100, "hello-world-again"),
    (17, "hello-world-again"),
    (16, "hello-world"),
    (12, "hello-world"),
    (11, "hello-world"),
    (10, "hello"),
    (5, "hello"),
    (3, "hel"),
])
def test_max_length(limit, expected):
    assert slugify("Hello World Again", max_length=limit) == expected

def test_long_first_word_truncated():
    assert slugify("Supercalifragilistic expialidocious", max_length=5) == "super"

def test_custom_separator():
    assert slugify("The Quick Brown Fox", separator="_", max_length=15) == "the_quick_brown"
    assert slugify("The Quick Brown Fox", separator="_", max_length=14) == "the_quick"

def test_never_ends_with_separator():
    for n in range(1, 40):
        out = slugify("Ten tips for writing better release notes", max_length=n)
        assert len(out) <= n
        assert not out.endswith("-") and not out.startswith("-")
        assert "ten-tips-for-writing-better-release-notes".startswith(out)

def test_keyword_only_usage_unchanged_default():
    assert slugify("A  B") == "a-b"
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


require_tests("test_textutils.py", [
    "test_slugify_basic", "test_slugify_unicode", "test_slugify_separator", "test_excerpt", "test_reading_time",
])
run_hidden_tests(HIDDEN)
sys.exit(0)
