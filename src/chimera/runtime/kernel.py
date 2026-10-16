from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from chimera.agents.base import Agent, AgentContext
from chimera.events import Event, EventStore, InMemoryEventStore
from chimera.plugins import ToolRegistry
from chimera.runtime.budget import Budget
from chimera.runtime.history import SCHEMA_VERSION, History, budget_snapshot, process_snapshot
from chimera.runtime.process import Process, ProcessState
from chimera.security import Policy

_RUNNABLE = {ProcessState.READY, ProcessState.WAITING}
_RESERVED = {"process.registered", "process.failed"}


class _CheckpointError(RuntimeError):
    """A failed durable budget charge must not be treated as a worker failure."""


class Kernel:
    """Deterministic single-owner kernel with durable, at-least-once delivery.

    Construction/replay projects history without invoking workers or tools. Reattach
    workers and configure tools/policy explicitly before resuming with run().
    """

    def __init__(self, *, event_store: EventStore | None = None) -> None:
        # Empty stores are falsey; do not accidentally replace the supplied store.
        self.events: EventStore = event_store if event_store is not None else InMemoryEventStore()
        self.policy = Policy()
        self.tools = ToolRegistry(self.policy)
        self._agents: dict[str, Agent] = {}
        self._processes: dict[str, Process] = {}
        self._subscriptions: dict[str, frozenset[str]] = {}
        self._queue: deque[Event] = deque()
        self._targets: dict[UUID, tuple[str, ...]] = {}
        self._delivered: set[tuple[UUID, str]] = set()
        self._running = False
        self._needs_replay = False
        self.replay()

    def register_agent(
        self, *, process_id: str, agent: Agent,
        parent_process_id: str | None = None, budget: Budget | None = None,
    ) -> Process:
        self._require_idle()
        if process_id in self._processes:
            raise ValueError(f"process already exists: {process_id}; use attach_agent to recover")
        owned_budget = Budget() if budget is None else Budget(
            max_steps=budget.max_steps, max_tool_calls=budget.max_tool_calls,
            max_tokens=budget.max_tokens, steps_used=budget.steps_used,
            tool_calls_used=budget.tool_calls_used, tokens_used=budget.tokens_used,
        )
        process = Process(
            process_id=process_id, agent_type=agent.agent_type,
            parent_process_id=parent_process_id,
            budget=owned_budget,
        )
        process.transition(ProcessState.READY)
        event = self._record(
            "process.registered",
            {**process_snapshot(process), "subscriptions": sorted(agent.subscriptions)},
        )
        event = self._append(event)
        self._processes[process_id] = process
        self._subscriptions[process_id] = frozenset(agent.subscriptions)
        self._agents[process_id] = agent
        self._bind_process(process)
        self._queue.append(event)
        return process

    def attach_agent(self, *, process_id: str, agent: Agent) -> Process:
        """Bind implementation code to a recovered identity; never append registration."""
        self._require_idle()
        process = self._processes[process_id]
        if agent.agent_type != process.agent_type:
            raise ValueError("agent_type does not match recovered process")
        if frozenset(agent.subscriptions) != self._subscriptions[process_id]:
            raise ValueError("subscriptions do not match recovered process")
        self._agents[process_id] = agent
        return process

    def publish(self, event: Event) -> None:
        self._require_idle()
        self._validate_public_event(event)
        self._queue.append(self._append(event))

    def replay(self) -> None:
        """Rebuild process state, budgets and pending deliveries, with no side effects.

        Existing Process references become stale. Policy/tools and attached worker
        implementations are retained, but grants are never inferred from history.
        """
        if self._running:
            raise RuntimeError("cannot replay a running kernel")
        self._needs_replay = True
        history = History.replay(self.events.read_from())
        self._processes = history.processes
        self._subscriptions = history.subscriptions
        self._targets = history.targets
        self._delivered = history.delivered
        self._queue = deque(history.pending())
        for process in self._processes.values():
            self._bind_process(process)
        self._needs_replay = False

    def run(self, *, max_deliveries: int = 10_000) -> int:
        self._require_idle()
        if max_deliveries < 0:
            raise ValueError("max_deliveries must be >= 0")
        deliveries = 0
        self._running = True
        try:
            while self._queue:
                # Keep the event until every recipient has a committed receipt.
                event = self._queue[0]
                if event.event_id not in self._targets:
                    targets = tuple(
                        pid for pid, process in self._processes.items()
                        if event.event_type in self._subscriptions[pid]
                        and process.state in _RUNNABLE
                    )
                    self._append(self._record(
                        "runtime.event_dispatched",
                        {"input_event_id": str(event.event_id), "process_ids": list(targets)},
                    ))
                    self._targets[event.event_id] = targets
                for process_id in self._targets[event.event_id]:
                    if (event.event_id, process_id) in self._delivered:
                        continue
                    process = self._processes[process_id]
                    if process.state not in _RUNNABLE:
                        self._commit_delivery(event, process, (), outcome="skipped")
                        continue
                    if process_id not in self._agents:
                        raise RuntimeError(f"recovered worker is not attached: {process_id}")
                    if deliveries >= max_deliveries:
                        raise RuntimeError("max event deliveries exceeded")
                    self._deliver(event, process, self._agents[process_id])
                    deliveries += 1
                self._queue.popleft()
        except BaseException:
            # Includes crashes simulated with SystemExit/KeyboardInterrupt. A
            # storage failure must never leave the in-memory projection runnable.
            self._needs_replay = True
            raise
        finally:
            self._running = False
        return deliveries

    def _deliver(self, event: Event, process: Process, agent: Agent) -> None:
        prior_state = process.state
        process.transition(ProcessState.RUNNING)
        self._append(self._record(
            "runtime.delivery_started",
            {
                "input_event_id": str(event.event_id),
                "prior_state": prior_state.value,
                "process": process_snapshot(process),
            },
        ))
        context = AgentContext(process=process, policy=self.policy, tools=self.tools)
        outcome = "completed"
        try:
            # Persist every charge before worker/tool execution, including failed
            # attempts. A retry consumes another step rather than resetting limits.
            process.budget.charge_step()
            emitted = tuple(agent.handle(event.snapshot(), context))
        except _CheckpointError:
            raise
        except Exception as exc:
            process.last_error = f"{type(exc).__name__}: {exc}"
            # A worker can transition then throw. None of its lifecycle/output is
            # committed yet, so fail the attempt even if it locally completed.
            process.state = ProcessState.RUNNING
            process.transition(ProcessState.FAILED)
            emitted = (event.child(
                event_type="process.failed", actor_id="kernel",
                payload={"process_id": process.process_id, "error": process.last_error},
            ),)
            outcome = "failed"
        else:
            if process.state is ProcessState.RUNNING:
                process.transition(ProcessState.READY)
            # Validate all children before committing any of them.
            for child in emitted:
                self._validate_public_event(child)
                if child.correlation_id != event.correlation_id:
                    raise ValueError("agents must preserve correlation_id")
                if child.causation_id != event.event_id:
                    raise ValueError("agents must set causation_id to the handled event")
        if self._needs_replay:
            raise _CheckpointError("worker suppressed a failed durable checkpoint")
        self._commit_delivery(event, process, emitted, outcome=outcome)

    def _commit_delivery(
        self, event: Event, process: Process, emitted: Iterable[Event], *, outcome: str,
    ) -> None:
        children = tuple(emitted)
        receipt = self._record("runtime.delivery_completed", {
            "input_event_id": str(event.event_id),
            "process": process_snapshot(process),
            "outcome": outcome,
        })
        # Output events and the receipt are one transaction. After a crash there
        # can be neither orphaned output nor an acknowledgement without output.
        offsets = self.events.append_many((receipt, *children))
        self._delivered.add((event.event_id, process.process_id))
        if children:
            self._queue.extend(self.events.read_from(offsets[1])[:len(children)])

    def _bind_process(self, process: Process) -> None:
        def checkpoint() -> None:
            try:
                if self._processes.get(process.process_id) is not process:
                    raise RuntimeError("stale process reference; fetch it again after replay")
                if self._needs_replay:
                    raise RuntimeError("kernel requires replay after an interrupted run")
                self._append(self._record("runtime.budget_updated", {
                    "process_id": process.process_id,
                    "budget": budget_snapshot(process.budget),
                }))
            except Exception as exc:
                self._needs_replay = True
                raise _CheckpointError("could not persist budget charge") from exc
        process.budget._on_charge = checkpoint

        def checkpoint_transition() -> None:
            if self._running:
                return  # Handler lifecycle commits together with its output.
            try:
                self._require_idle()
                if self._processes.get(process.process_id) is not process:
                    raise RuntimeError("stale process reference; fetch it again after replay")
                self._append(self._record("runtime.process_updated", {
                    "process": process_snapshot(process),
                }))
            except Exception:
                self._needs_replay = True
                raise
        process._on_transition = checkpoint_transition

    def _append(self, event: Event) -> Event:
        try:
            offset = self.events.append(event)
            return self.events.read_from(offset)[0]
        except BaseException:
            self._needs_replay = True
            raise

    @staticmethod
    def _record(event_type: str, payload: dict[str, Any]) -> Event:
        # Journal records have their own trace; input_event_id explicitly links
        # them to domain events without polluting domain correlation queries.
        return Event(
            event_type=event_type, actor_id="kernel",
            payload={"schema_version": SCHEMA_VERSION, **payload},
        )

    @staticmethod
    def _validate_public_event(event: Event) -> None:
        if event.event_type.startswith("runtime.") or event.event_type in _RESERVED:
            raise ValueError(f"event type is reserved for the kernel: {event.event_type}")

    def _require_idle(self) -> None:
        if self._running:
            raise RuntimeError("kernel is already running")
        if self._needs_replay:
            raise RuntimeError("kernel requires replay after an interrupted run")

    def process(self, process_id: str) -> Process:
        return self._processes[process_id]

    def process_table(self) -> tuple[Process, ...]:
        return tuple(self._processes.values())
