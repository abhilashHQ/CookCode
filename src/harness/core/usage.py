from typing import Literal

from pydantic import BaseModel, ConfigDict

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "aborted", "error"]


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input=self.input + other.input,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
            output=self.output + other.output,
            reasoning=self.reasoning + other.reasoning,
        )

    @property
    def total_input(self) -> int:
        return self.input + self.cache_read + self.cache_write
