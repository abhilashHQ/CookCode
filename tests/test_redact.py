import pytest

from harness.redact import Redactor

FAKES = {
    "anthropic_key": "sk-ant-" + "a1B2" * 10,
    "openrouter_key": "sk-or-v1-" + "f0" * 32,
    "openai_key": "sk-proj-" + "Zx9_" * 8,
    "aws_access_key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github_token": "ghp_" + "a" * 36,
    "google_api_key": "AIza" + "b" * 35,
    "slack_token": "xoxb-" + "1234567890-abc",
    "bearer_token": "Bearer " + "t0ken." * 5,
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
}


@pytest.mark.parametrize("kind", sorted(FAKES))
def test_each_pattern_is_redacted(kind: str) -> None:
    text = f"before {FAKES[kind]} after"
    redacted = Redactor().redact_text(text)
    assert FAKES[kind] not in redacted
    assert f"[REDACTED:{kind}]" in redacted
    assert redacted.startswith("before ")
    assert redacted.endswith(" after")


def test_explicit_secret_values_are_redacted() -> None:
    redactor = Redactor(secret_values=["hunter2-database-password"])
    assert redactor.redact_text("pw=hunter2-database-password") == "pw=[REDACTED:secret_value]"


def test_short_secret_values_are_ignored() -> None:
    redactor = Redactor(secret_values=["abc"])
    assert redactor.redact_text("abc abc") == "abc abc"


def test_from_env_picks_named_variables_only() -> None:
    environ = {"DB_PASSWORD": "correct-horse-battery", "HOME": "/home/me"}
    redactor = Redactor.from_env(["DB_PASSWORD", "MISSING"], environ)
    assert redactor.redact_text("correct-horse-battery /home/me") == (
        "[REDACTED:secret_value] /home/me"
    )


def test_nested_structures_are_walked() -> None:
    key = FAKES["openai_key"]
    value = {"a": [key, {"b": f"x {key}"}], "n": 3, "ok": True, "none": None}
    assert Redactor().redact(value) == {
        "a": ["[REDACTED:openai_key]", {"b": "x [REDACTED:openai_key]"}],
        "n": 3,
        "ok": True,
        "none": None,
    }


def test_ordinary_text_is_untouched() -> None:
    text = "run sk-short and ask the task-runner about AKIA"
    assert Redactor().redact_text(text) == text


def test_redaction_is_idempotent() -> None:
    once = Redactor().redact_text(f"k={FAKES['anthropic_key']}")
    assert Redactor().redact_text(once) == once
