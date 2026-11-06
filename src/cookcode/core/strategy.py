from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StrategyName = Literal["raw", "mask", "mask_batched", "summarize", "hybrid"]
DEFAULT_TRIGGER = 48_000


class Strategy(BaseModel):
    """How the context builder shrinks history (spec section 7). Recorded in the run config so
    replay rebuilds each request under the strategy that built it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrategyName = "raw"
    keep_steps: int = Field(default=6, ge=1)
    batch_steps: int = Field(default=8, ge=1)
    trigger_tokens: int = Field(default=DEFAULT_TRIGGER, ge=1)

    @property
    def masks(self) -> bool:
        return self.name in ("mask", "mask_batched", "hybrid")

    @property
    def batched(self) -> bool:
        return self.name in ("mask_batched", "hybrid")

    @property
    def summarizes(self) -> bool:
        return self.name in ("summarize", "hybrid")

    @property
    def label(self) -> str:
        if self.name == "raw":
            return "raw"
        parts = [self.name, f"k{self.keep_steps}"]
        if self.batched:
            parts.append(f"b{self.batch_steps}")
        if self.summarizes:
            parts.append(f"t{self.trigger_tokens}")
        return ":".join(parts)

    def for_window(self, window: int) -> "Strategy":
        """A summarizing strategy without an explicit trigger summarizes at half the context
        window, capped at the default so cloud runs keep the trigger they were measured with."""
        if not self.summarizes or "trigger_tokens" in self.model_fields_set:
            return self
        return self.model_copy(update={"trigger_tokens": min(DEFAULT_TRIGGER, window // 2)})

    @classmethod
    def parse(cls, text: str) -> "Strategy":
        """`raw`, `mask`, `mask:k4`, `mask_batched:k6:b8`, `hybrid:k6:b8:t40000`."""
        name, *options = text.split(":")
        fields: dict[str, object] = {"name": name}
        keys = {"k": "keep_steps", "b": "batch_steps", "t": "trigger_tokens"}
        for option in options:
            key = keys.get(option[:1])
            if key is None or not option[1:].isdigit():
                raise ValueError(f"bad strategy option {option!r} in {text!r}")
            fields[key] = int(option[1:])
        return cls.model_validate(fields)
