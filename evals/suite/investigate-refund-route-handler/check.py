import hashlib
import re
import sys
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()
ANSWER_PATH = Path(sys.argv[2])
ANSWER = ANSWER_PATH.read_text(errors="replace") if ANSWER_PATH.is_file() else ""

HASHES = {
    'app/billing/approvals.py': 'cfaf8f8f811764dfe83aacbe03679f848a0426cc8990bf981f8aaa677168e596',
    'app/billing/payments.py': 'a1dbf19bb29659894bd32c37fcf67506e2cb03dc355099d6dbead6f3c71ad784',
    'app/billing/refunds.py': '6e773419fba9783c99d62664bcb55584a186c0ec18255201e655dd771f37794c',
    'app/main.py': '8a5609b12eef8bb31730f75e08fa5986d36ac1e8c490ea2aea7af88699663c45',
    'app/routes.py': 'bd761a53c40de9d15e9b072f79318999670215fa8e116a5a08c2821e72207369',
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
if "issue_refund" not in text:
    fail("answer does not name issue_refund")
if not re.search(r"approvals(\.py)?", text):
    fail("answer does not point to approvals.py")
sys.exit(0)
