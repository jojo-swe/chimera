from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from chimera.events import Event
from chimera.runtime.budget import Budget
from chimera.runtime.process import Process, ProcessState

SCHEMA_VERSION = 1
BUDGET_FIELDS = (
    "max_steps", "max_tool_calls", "max_tokens",
    "steps_used", "tool_calls_used", "tokens_used",
)
_RUNNABLE = {ProcessState.READY, ProcessState.WAITING}


def budget_snapshot(budget: Budget) -> dict[str, int]:
    return {name: getattr(budget, name) for name in BUDGET_FIELDS}


def process_snapshot(process: Process) -> dict[str, Any]:
    return {
        "process_id": process.process_id,
        "agent_type": process.agent_type,
        "parent_process_id": process.parent_process_id,
        "state": process.state.value,
        "last_error": process.last_error,
        "budget": budget_snapshot(process.budget),
    }


def _restore_budget(snapshot: Mapping[str, Any]) -> Budget:
    if set(snapshot) != set(BUDGET_FIELDS) or any(
        type(snapshot[name]) is not int or snapshot[name] < 0 for name in BUDGET_FIELDS
    ):
        raise ValueError("invalid budget snapshot")
    return Budget(**snapshot)


def restore_process(snapshot: Mapping[str, Any]) -> Process:
    for name in ("process_id", "agent_type"):
        if not isinstance(snapshot.get(name), str) or not snapshot[name]:
            raise ValueError(f"invalid process {name}")
    for name in ("parent_process_id", "last_error"):
        if snapshot.get(name) is not None and not isinstance(snapshot[name], str):
            raise ValueError(f"invalid process {name}")
    return Process(
        process_id=snapshot["process_id"],
        agent_type=snapshot["agent_type"],
        parent_process_id=snapshot.get("parent_process_id"),
        state=ProcessState(snapshot["state"]),
        last_error=snapshot.get("last_error"),
        budget=_restore_budget(snapshot["budget"]),
    )


def _check_budget_progress(previous: Budget, current: Budget) -> None:
    if any(
        getattr(current, name) < getattr(previous, name)
        for name in ("steps_used", "tool_calls_used", "tokens_used")
    ):
        raise ValueError("budget counters cannot decrease in history")


@dataclass(slots=True)
class History:
    """Read-only projection: never calls agents, tools, or store.append."""

    processes: dict[str, Process] = field(default_factory=dict)
    subscriptions: dict[str, frozenset[str]] = field(default_factory=dict)
    events: dict[UUID, Event] = field(default_factory=dict)
    targets: dict[UUID, tuple[str, ...]] = field(default_factory=dict)
    delivered: set[tuple[UUID, str]] = field(default_factory=set)

    @classmethod
    def replay(cls, events: Iterable[Event]) -> History:
        history = cls()
        # At most one in-flight input per process. A second start of the same pair
        # is a legitimate retry after a crash; a different input is not.
        interrupted: dict[str, tuple[UUID, ProcessState]] = {}
        for event in events:
            if not event.event_type.startswith("runtime."):
                history.events[event.event_id] = event
            if event.event_type == "process.registered":
                cls._check_version(event)
                process = restore_process(event.payload)
                if process.process_id in history.processes:
                    raise ValueError(f"duplicate process registration: {process.process_id}")
                if process.state is not ProcessState.READY:
                    raise ValueError("registered process must be ready")
                subscriptions = event.payload["subscriptions"]
                if not isinstance(subscriptions, list) or any(
                    not isinstance(value, str) for value in subscriptions
                ):
                    raise ValueError("invalid process subscriptions")
                history.processes[process.process_id] = process
                history.subscriptions[process.process_id] = frozenset(subscriptions)
            elif event.event_type.startswith("runtime."):
                cls._check_version(event)
                payload = event.payload
                if event.event_type == "runtime.event_dispatched":
                    input_id = UUID(payload["input_event_id"])
                    if input_id not in history.events or input_id in history.targets:
                        raise ValueError("invalid or duplicate dispatch record")
                    targets = tuple(payload["process_ids"])
                    if len(set(targets)) != len(targets):
                        raise ValueError("duplicate dispatch target")
                    if any(pid not in history.processes for pid in targets):
                        raise ValueError("dispatch references unknown process")
                    history.targets[input_id] = targets
                elif event.event_type in {
                    "runtime.delivery_started", "runtime.delivery_completed",
                }:
                    input_id = UUID(payload["input_event_id"])
                    process = restore_process(payload["process"])
                    process_id = process.process_id
                    if process_id not in history.targets.get(input_id, ()):
                        raise ValueError("delivery references unknown dispatch target")
                    key = (input_id, process_id)
                    if key in history.delivered:
                        raise ValueError("delivery already completed")
                    active = interrupted.get(process_id)
                    if event.event_type == "runtime.delivery_started":
                        prior_state = ProcessState(payload["prior_state"])
                        if prior_state not in _RUNNABLE or (
                            process.state is not ProcessState.RUNNING
                        ):
                            raise ValueError("invalid delivery start state")
                        if active is not None and active != (input_id, prior_state):
                            raise ValueError("process already has another in-flight delivery")
                        interrupted[process_id] = (input_id, prior_state)
                    else:
                        outcome = payload["outcome"]
                        if outcome == "skipped":
                            previous = history.processes[process_id]
                            if active is not None or process.state in _RUNNABLE or (
                                process.state is not previous.state
                            ):
                                raise ValueError("invalid skipped delivery")
                        elif outcome in {"completed", "failed"}:
                            if active is None or active[0] != input_id:
                                raise ValueError("completion has no matching delivery start")
                            if process.state in {ProcessState.CREATED, ProcessState.RUNNING}:
                                raise ValueError("invalid completion state")
                            if outcome == "failed" and (
                                process.state is not ProcessState.FAILED or not process.last_error
                            ):
                                raise ValueError("invalid failed delivery")
                        else:
                            raise ValueError("unknown delivery outcome")
                        history.delivered.add(key)
                        interrupted.pop(process_id, None)
                    history._replace_process(process)
                elif event.event_type == "runtime.budget_updated":
                    process = history.processes[payload["process_id"]]
                    budget = _restore_budget(payload["budget"])
                    _check_budget_progress(process.budget, budget)
                    process.budget = budget
                elif event.event_type == "runtime.process_updated":
                    process = restore_process(payload["process"])
                    history._replace_process(process)
                    # Explicit control after recovery supersedes an interrupted
                    # attempt's prior state, without acknowledging that input.
                    interrupted.pop(process.process_id, None)
                else:
                    raise ValueError(f"unknown runtime record: {event.event_type}")
        # In-flight attempts have no committed output. Retain their budget charges,
        # restore the pre-attempt runnable state, and let run() retry them.
        for process_id, (_, state) in interrupted.items():
            history.processes[process_id].state = state
        return history

    def _replace_process(self, process: Process) -> None:
        if process.process_id not in self.processes:
            raise ValueError("snapshot references unknown process")
        previous = self.processes[process.process_id]
        if (process.agent_type, process.parent_process_id) != (
            previous.agent_type, previous.parent_process_id,
        ):
            raise ValueError("process identity changed in history")
        _check_budget_progress(previous.budget, process.budget)
        self.processes[process.process_id] = process

    @staticmethod
    def _check_version(event: Event) -> None:
        if event.actor_id != "kernel" or event.payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported or untrusted runtime history record")

    def pending(self) -> tuple[Event, ...]:
        return tuple(
            event for event_id, event in self.events.items()
            if event_id not in self.targets
            or any((event_id, pid) not in self.delivered for pid in self.targets[event_id])
        )
