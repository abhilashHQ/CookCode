import hashlib
import re
import sys
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
ANSWER_PATH = Path(sys.argv[2])
ANSWER = ANSWER_PATH.read_text(errors="replace") if ANSWER_PATH.is_file() else ""

HASHES = {
    'README.md': '139ce16ee372c1138251bd5b9170537ba8ee5599e4ad01fcd80760964b0e9963',
    'config/base.ini': '2987e6c1c57ca4dfdd05e8e1d623e7957e1edd0f9327120008d6962c56ff57fd',
    'config/production.ini': 'dc555d9c2a201f80d254227eede216540cc4b6800050f1c140b37d4aac1805d9',
    'svc/defaults.py': 'c175cb1842910192c997c5a57462527a0f08ee2c518a81be93cef1b4ed436d23',
    'svc/http.py': '15c18780cd10533abf27f5ac933a983357a61a1d2387bed1bd49c03158ea9141',
    'svc/settings.py': '71f5f7233dbb4691f62163f8389e8b4b29211e1a92a579cd37183ca7278e3c28',
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
if not re.search(r"(?<![\d.])4\.5(?:0*)?(?![\d])|4500\s*(?:ms|milli)", text):
    fail("answer does not give the 4.5 second timeout")
sys.exit(0)
