from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


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
    _on_charge: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        values = (
            self.max_steps, self.max_tool_calls, self.max_tokens,
            self.steps_used, self.tool_calls_used, self.tokens_used,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("budget limits and counters must be non-negative integers")

    def charge_step(self, amount: int = 1) -> None:
        self._charge("steps_used", amount)

    def charge_tool_call(self, amount: int = 1) -> None:
        self._charge("tool_calls_used", amount)

    def charge_tokens(self, amount: int) -> None:
        self._charge("tokens_used", amount)

    def _charge(self, counter: str, amount: int) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("resource charge must be a non-negative integer")
        previous = getattr(self, counter)
        setattr(self, counter, previous + amount)
        try:
            if self._on_charge is not None:
                self._on_charge()
        except BaseException:
            # No tool may execute on an uncommitted reservation.
            setattr(self, counter, previous)
            raise
        self._check()

    def _check(self) -> None:
        if self.steps_used > self.max_steps:
            raise BudgetExceeded("step budget exceeded")
        if self.tool_calls_used > self.max_tool_calls:
            raise BudgetExceeded("tool-call budget exceeded")
        if self.tokens_used > self.max_tokens:
            raise BudgetExceeded("token budget exceeded")
