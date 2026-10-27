from pydantic import BaseModel, ConfigDict


class Features(BaseModel):
    """Optional harness features, all off by default so the baseline stays the measured harness.
    Each one is a toggle for an ablation; the enabled set is recorded with every run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    jobs: bool = False
    verify_first: bool = False
    notes: bool = False
    facts: bool = False
    subagents: bool = False
    skills: bool = False
    hooks: bool = False

    @property
    def label(self) -> str:
        enabled = [name for name, on in self.model_dump().items() if on]
        return "+".join(enabled) or "base"

    @classmethod
    def parse(cls, text: str) -> "Features":
        """`base` or a comma-separated list of feature names: `jobs,verify_first,facts`."""
        names = [name.strip() for name in text.split(",") if name.strip() not in ("", "base")]
        unknown = sorted(set(names) - set(cls.model_fields))
        if unknown:
            raise ValueError(f"unknown feature {', '.join(unknown)} in {text!r}")
        return cls.model_validate(dict.fromkeys(names, True))
