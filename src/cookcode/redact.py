import re
from collections.abc import Iterable, Mapping

type JSONValue = str | int | float | bool | dict[str, JSONValue] | list[JSONValue] | None

_NOT_IN_TOKEN = r"(?<![A-Za-z0-9_-])"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("anthropic_key", re.compile(_NOT_IN_TOKEN + r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openrouter_key", re.compile(_NOT_IN_TOKEN + r"sk-or-v1-[A-Za-z0-9]{32,}")),
    ("nvidia_key", re.compile(_NOT_IN_TOKEN + r"nvapi-[A-Za-z0-9_-]{32,}")),
    ("openai_key", re.compile(_NOT_IN_TOKEN + r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
)


class Redactor:
    def __init__(self, secret_values: Iterable[str] = (), min_secret_len: int = 8) -> None:
        unique = {value for value in secret_values if len(value) >= min_secret_len}
        self._values = sorted(unique, key=len, reverse=True)

    @classmethod
    def from_env(cls, names: Iterable[str], environ: Mapping[str, str]) -> "Redactor":
        return cls(environ[name] for name in names if name in environ)

    def redact_text(self, text: str) -> str:
        text = text.encode("utf-8", "backslashreplace").decode("utf-8")
        for value in self._values:
            text = text.replace(value, "[REDACTED:secret_value]")
        for kind, pattern in _PATTERNS:
            text = pattern.sub(f"[REDACTED:{kind}]", text)
        return text

    def redact(self, value: JSONValue) -> JSONValue:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {self.redact_text(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        return value
