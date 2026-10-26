from collections import deque
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from harness.core.cancel import CancelToken
from harness.core.events import SideEffect
from harness.core.items import ToolCall

NO_APPROVER = "no approver available; the policy did not allow this call"


class Risk(BaseModel):
    model_config = ConfigDict(frozen=True)

    side_effect: SideEffect
    rule: str | None
    reason: str
    cwd: str
    subjects: tuple[str, ...]


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["allow", "deny", "allow_always"]
    reason: str | None = None
    rule: str | None = None


class Approver(Protocol):
    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision: ...


class PolicyOnlyApprover:
    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        return Decision(action="deny", reason=NO_APPROVER)


class ScriptedApprover:
    def __init__(self, decisions: Sequence[Decision]) -> None:
        self._decisions = deque(decisions)
        self.seen: list[tuple[ToolCall, Risk]] = []

    async def decide(self, call: ToolCall, risk: Risk, cancel: CancelToken) -> Decision:
        self.seen.append((call, risk))
        if not self._decisions:
            raise RuntimeError("ScriptedApprover has no decisions left")
        return self._decisions.popleft()
