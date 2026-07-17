from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    """Raised when runtime-owned resource limits are exceeded."""


@dataclass(slots=True)
class Budget:
    max_steps: int = 100
    max_tool_calls: int = 20
    max_tokens: int = 100_000
    steps_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0

    def charge_step(self, amount: int = 1) -> None:
        self.steps_used += amount
        self._check()

    def charge_tool_call(self, amount: int = 1) -> None:
        self.tool_calls_used += amount
        self._check()

    def charge_tokens(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("token charge cannot be negative")
        self.tokens_used += amount
        self._check()

    def _check(self) -> None:
        if self.steps_used > self.max_steps:
            raise BudgetExceeded("step budget exceeded")
        if self.tool_calls_used > self.max_tool_calls:
            raise BudgetExceeded("tool-call budget exceeded")
        if self.tokens_used > self.max_tokens:
            raise BudgetExceeded("token budget exceeded")
