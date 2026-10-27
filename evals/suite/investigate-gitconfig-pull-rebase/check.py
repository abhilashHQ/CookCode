import hashlib
import re
import sys
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
ANSWER_PATH = Path(sys.argv[2])
ANSWER = ANSWER_PATH.read_text(errors="replace") if ANSWER_PATH.is_file() else ""

HASHES = {
    'README.md': '0eb03c3a085107aed77df8612fd5222e99920c0609e9781a93e034772ab6d8a0',
    'git/aliases.inc': 'cb88363f5aa3326069421e0cd1ac4d9da5951468f0eadb0df22310681502bc28',
    'git/legacy.inc': '7f715de9fb334830310b832274f83d5a5939416b38c45bc8dfe5739cfcee6979',
    'git/work.inc': '1f03579604b183d90bd9bf8adc94ee8cb848e0a4b4c4f080e7017bb62f79d20f',
    'gitconfig': 'd1c1dde4005fed5d3bad8824189da68815280ff9d659ef9578a3992227dc2a8b',
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
if not re.search(r"\bfalse\b", text):
    fail("answer does not say pull.rebase resolves to false")
cue = re.search(
    r"(prints?|outputs?|resolves? to|evaluates? to|returns?|answer(?: is)?|result(?: is)?|final(?: value)?(?: is)?)"
    r"\W{0,5}(false|true|merges)\b",
    text,
)
if cue and cue.group(2) != "false":
    fail(f"answer says the final value is {cue.group(2)}")
sys.exit(0)
