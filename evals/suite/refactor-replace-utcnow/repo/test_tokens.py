from datetime import datetime, timedelta

import pytest

import audit
import tokens


def test_roundtrip():
    token = tokens.issue("billing")
    assert tokens.verify(token)["sub"] == "billing"


def test_expired():
    past = datetime.utcnow() - timedelta(hours=1)
    token = tokens.issue("billing", now=past)
    with pytest.raises(ValueError):
        tokens.verify(token)


def test_tampered():
    token = tokens.issue("billing")
    with pytest.raises(ValueError):
        tokens.verify(token[:-2] + "xx")


def test_audit_record():
    log = []
    entry = audit.record(log, "ana", "login", when=datetime(2024, 1, 2, 3, 4, 5))
    assert entry["at"] == "2024-01-02T03:04:05Z"
    assert audit.since(log, datetime(2024, 1, 1)) == [entry]
