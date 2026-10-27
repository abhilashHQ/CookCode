import hashlib
import re
import sys
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
ANSWER_PATH = Path(sys.argv[2])
ANSWER = ANSWER_PATH.read_text(errors="replace") if ANSWER_PATH.is_file() else ""

HASHES = {
    'README.md': 'da4a323b7ac14f9d0ac7f7c373a3c47ca135d0c4b0e8a0278485a9584a4680e5',
    'logs/access.log': '805783a1019f9ddede3b2a55392f2313cf29a2e1ba93b0ff30be8c1e15760e29',
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
if not re.search(r"(?<![\d.])172\.16\.9\.3(?![\d])", text):
    fail("answer does not name 172.16.9.3")
if not re.search(r"(?<![\d.])(7|seven)(?![\d.])", text):
    fail("answer does not give the count of 7")
sys.exit(0)
