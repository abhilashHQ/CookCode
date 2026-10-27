import hashlib
import re
import sys
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
ANSWER_PATH = Path(sys.argv[2])
ANSWER = ANSWER_PATH.read_text(errors="replace") if ANSWER_PATH.is_file() else ""

HASHES = {
    '.github/workflows/ci.yml': 'c0606faca2fd9f56f43ca04c315fd1339edb401f0ac455c03942db9e1a09cc7e',
    'blog/posts.py': 'e0e165dd237d757f4aeed7424c8fcb6195f9d415c5f2d0f24a80921473e3596e',
    'blog/tags.py': 'e73837869a133f8f76d41fb5a4774313a161620047116dd1831b6cd1afd25c06',
    'pyproject.toml': '365ac4c9124dd41998b390169a9f57ac221fb145310dfc6d75ea1d3520c00e1b',
    'tests/test_tags.py': '5511452790f92a913fd4c13bc80512517b469d9d9a2b08735ef8c14f8c2d3ac1',
}

IGNORE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def fail(msg):
    print(msg)
    sys.exit(1)


def snapshot():
    out = {}
    for p in WORKDIR.rglob("*"):
        rel = p.relative_to(WORKDIR)
        if not p.is_file() or any(part in IGNORE_DIRS for part in rel.parts) or p.suffix == ".pyc":
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


current = snapshot()
if current != HASHES:
    changed = sorted(set(current) ^ set(HASHES) | {k for k in current if k in HASHES and current[k] != HASHES[k]})
    fail(f"repo was modified: {', '.join(changed[:5])}")

text = ANSWER.lower()
if "merge_tags" not in text:
    fail("answer does not identify merge_tags as the culprit")
if not re.search(r"\bsets?\b|set\(|hash", text):
    fail("answer does not mention the set / hashing")
if not re.search(r"order|hashseed|random|nondetermin|non-determin|unordered", text):
    fail("answer does not explain the ordering nondeterminism")
sys.exit(0)
