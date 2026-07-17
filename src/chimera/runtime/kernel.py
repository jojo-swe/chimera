from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from chimera.agents.base import Agent, AgentContext
from chimera.events import Event, InMemoryEventStore
from chimera.plugins import ToolRegistry
from chimera.runtime.process import Process, ProcessState
from chimera.security import Policy


@dataclass(slots=True)
class _RegisteredAgent:
    agent: Agent
    process: Process


class Kernel:
    """Deterministic single-node Chimera kernel."""

    def __init__(self) -> None:
        self.events = InMemoryEventStore()
        self.policy = Policy()
        self.tools = ToolRegistry(self.policy)
        self._agents: dict[str, _RegisteredAgent] = {}
        self._queue: deque[Event] = deque()

    def register_agent(self, *, process_id: str, agent: Agent, parent_process_id: str | None = None) -> Process:
        if process_id in self._agents:
            raise ValueError(f"process already exists: {process_id}")
        process = Process(process_id=process_id, agent_type=agent.agent_type, parent_process_id=parent_process_id)
        process.transition(ProcessState.READY)
        self._agents[process_id] = _RegisteredAgent(agent=agent, process=process)
        self.publish(Event(event_type="process.registered", actor_id="kernel", payload={"process_id": process_id, "agent_type": agent.agent_type}))
        return process

    def publish(self, event: Event) -> None:
        self.events.append(event)
        self._queue.append(event)

    def run(self, *, max_deliveries: int = 10_000) -> int:
        deliveries = 0
        while self._queue:
            event = self._queue.popleft()
            for registered in tuple(self._agents.values()):
                if event.event_type not in registered.agent.subscriptions:
                    continue
                if registered.process.state not in {ProcessState.READY, ProcessState.WAITING}:
                    continue
                deliveries += 1
                if deliveries > max_deliveries:
                    raise RuntimeError("max event deliveries exceeded")
                process = registered.process
                process.transition(ProcessState.RUNNING)
                process.budget.charge_step()
                context = AgentContext(process=process, policy=self.policy, tools=self.tools)
                try:
                    emitted = registered.agent.handle(event, context)
                except Exception as exc:
                    process.last_error = f"{type(exc).__name__}: {exc}"
                    process.transition(ProcessState.FAILED)
                    self.publish(event.child(event_type="process.failed", actor_id="kernel", payload={"process_id": process.process_id, "error": process.last_error}))
                    continue
                if process.state is ProcessState.RUNNING:
                    process.transition(ProcessState.READY)
                for child in emitted:
                    if child.correlation_id != event.correlation_id:
                        raise ValueError("agents must preserve correlation_id")
                    if child.causation_id != event.event_id:
                        raise ValueError("agents must set causation_id to the handled event")
                    self.publish(child)
        return deliveries

    def process(self, process_id: str) -> Process:
        return self._agents[process_id].process

    def process_table(self) -> tuple[Process, ...]:
        return tuple(item.process for item in self._agents.values())
