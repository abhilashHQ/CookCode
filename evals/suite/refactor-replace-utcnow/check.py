import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import re
import warnings
from datetime import datetime, timedelta, timezone

import pytest

import audit
import tokens

UTC = timezone.utc

@pytest.fixture(autouse=True)
def strict_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        yield

def is_utc(dt):
    return dt.tzinfo is not None and dt.utcoffset() == timedelta(0)

def test_roundtrip_default_now():
    token = tokens.issue("billing")
    assert tokens.verify(token)["sub"] == "billing"

def test_expired_with_aware_now():
    past = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(ValueError):
        tokens.verify(tokens.issue("billing", now=past))

def test_tampered():
    with pytest.raises(ValueError):
        tokens.verify(tokens.issue("billing")[:-2] + "xx")

def test_expires_at_is_aware_and_exact():
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    token = tokens.issue("x", ttl=timedelta(minutes=30), now=now)
    exp = tokens.expires_at(token)
    assert is_utc(exp) and exp == datetime(2024, 6, 1, 12, 30, tzinfo=UTC)

def test_non_utc_aware_now():
    now = datetime(2024, 6, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    token = tokens.issue("x", ttl=timedelta(minutes=5), now=now)
    assert tokens.expires_at(token) == datetime(2024, 6, 1, 12, 5, tzinfo=UTC)
    assert tokens.verify(token, now=datetime(2024, 6, 1, 12, 4, tzinfo=UTC))["sub"] == "x"
    with pytest.raises(ValueError):
        tokens.verify(token, now=datetime(2024, 6, 1, 12, 5, tzinfo=UTC))

def test_token_wire_format_unchanged():
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    token = tokens.issue("billing", ttl=timedelta(minutes=15), now=now)
    import base64, json
    body = base64.urlsafe_b64encode(
        json.dumps({"exp": 1717244100, "sub": "billing"}, sort_keys=True).encode()
    ).rstrip(b"=").decode()
    assert token.split(".")[0] == body

def test_audit_record_and_parse():
    log = []
    entry = audit.record(log, "ana", "login", when=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert entry["at"] == "2024-01-02T03:04:05Z"
    parsed = audit.parse_stamp(entry["at"])
    assert is_utc(parsed) and parsed == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert audit.since(log, datetime(2024, 1, 1, tzinfo=UTC)) == [entry]
    assert audit.since(log, datetime(2024, 1, 3, tzinfo=UTC)) == []

def test_stamp_format_and_value():
    text = audit.stamp()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", text)
    delta = abs(audit.parse_stamp(text) - datetime.now(UTC))
    assert delta < timedelta(seconds=5)

def test_record_default_when():
    entry = audit.record([], "ben", "logout")
    assert abs(audit.parse_stamp(entry["at"]) - datetime.now(UTC)) < timedelta(seconds=5)
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


for path in WORKDIR.rglob("*.py"):
    if ".git" in path.parts:
        continue
    text = path.read_text()
    for old in ("utcnow", "utcfromtimestamp"):
        if old in text:
            fail(f"{path.relative_to(WORKDIR)} still uses {old}")

tests = WORKDIR / "test_tokens.py"
if not tests.is_file():
    fail("test_tokens.py was deleted")
for name in ("test_roundtrip", "test_expired", "test_tampered", "test_audit_record"):
    if f"def {name}(" not in tests.read_text():
        fail(f"test_tokens.py lost {name}")

run_hidden_tests(HIDDEN)
sys.exit(0)
