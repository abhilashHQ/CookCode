import hashlib
import re
import sys
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
ANSWER_PATH = Path(sys.argv[2])
ANSWER = ANSWER_PATH.read_text(errors="replace") if ANSWER_PATH.is_file() else ""

HASHES = {
    'CHANGELOG.md': '9b7a9d07a78defbfee092a064d356dc4d97bd07ebef7e181539fdad29bfab5e4',
    'apiclient/client.py': 'fb1651870f73de1348290fdd3cbbe0e0272e650e1f40e02d5844df9685f52248',
    'apiclient/config.py': '69d16ba981bd535d0f4a1a1bf2b881cedfe093eef8f012431d4a3f7b6d5ab3c4',
    'apiclient/throttle.py': 'b343d59934049f159f875993e4ee96a83028d14ecc0fe50029e1ebe5e53868ea',
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
if not re.search(r"(?<![\d.])1\.6(?:\.0)?(?![\d])", text):
    fail("answer does not name release 1.6.0")
sys.exit(0)
