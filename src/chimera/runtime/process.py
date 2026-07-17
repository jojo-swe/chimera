from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from chimera.runtime.budget import Budget


class ProcessState(StrEnum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


_ALLOWED_TRANSITIONS: dict[ProcessState, frozenset[ProcessState]] = {
    ProcessState.CREATED: frozenset({ProcessState.READY, ProcessState.TERMINATED}),
    ProcessState.READY: frozenset({ProcessState.RUNNING, ProcessState.PAUSED, ProcessState.TERMINATED}),
    ProcessState.RUNNING: frozenset({ProcessState.READY, ProcessState.WAITING, ProcessState.PAUSED, ProcessState.COMPLETED, ProcessState.FAILED, ProcessState.TERMINATED}),
    ProcessState.WAITING: frozenset({ProcessState.READY, ProcessState.PAUSED, ProcessState.FAILED, ProcessState.TERMINATED}),
    ProcessState.PAUSED: frozenset({ProcessState.READY, ProcessState.TERMINATED}),
    ProcessState.COMPLETED: frozenset(),
    ProcessState.FAILED: frozenset(),
    ProcessState.TERMINATED: frozenset(),
}


@dataclass(slots=True)
class Process:
    process_id: str
    agent_type: str
    budget: Budget = field(default_factory=Budget)
    state: ProcessState = ProcessState.CREATED
    parent_process_id: str | None = None
    last_error: str | None = None

    def transition(self, target: ProcessState) -> None:
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"invalid transition: {self.state} -> {target}")
        self.state = target
