from __future__ import annotations

from dataclasses import dataclass

from chimera.agents import AgentContext
from chimera.events import Event
from chimera.plugins import Tool
from chimera.runtime import Kernel, ProcessState
from chimera.security import Capability


@dataclass(frozen=True, slots=True)
class Planner:
    agent_type: str = "planner"
    subscriptions: frozenset[str] = frozenset({"task.created"})

    def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
        return (event.child(event_type="research.requested", actor_id=context.process.process_id, payload={"query": event.payload["goal"]}),)


@dataclass(frozen=True, slots=True)
class Researcher:
    agent_type: str = "researcher"
    subscriptions: frozenset[str] = frozenset({"research.requested"})

    def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
        result = context.tools.execute(tool_name="web-search", process=context.process, arguments={"query": event.payload["query"]})
        return (event.child(event_type="research.completed", actor_id=context.process.process_id, payload=result),)


@dataclass(frozen=True, slots=True)
class Reviewer:
    agent_type: str = "reviewer"
    subscriptions: frozenset[str] = frozenset({"research.completed"})

    def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
        context.process.transition(ProcessState.COMPLETED)
        return (event.child(event_type="task.completed", actor_id=context.process.process_id, payload={"approved": True, "summary": event.payload["summary"]}),)


def build_demo_kernel() -> Kernel:
    kernel = Kernel()
    kernel.tools.register(Tool(name="web-search", handler=lambda args: {"query": args["query"], "summary": "Chimera can coordinate guarded workers through auditable events.", "sources": ["mock://architecture-note"]}))
    kernel.register_agent(process_id="planner", agent=Planner())
    kernel.register_agent(process_id="researcher", agent=Researcher())
    kernel.register_agent(process_id="reviewer", agent=Reviewer())
    kernel.policy.grant(subject="researcher", capability=Capability(resource="tool:web-search", action="execute"))
    return kernel


def run_demo() -> Kernel:
    kernel = build_demo_kernel()
    kernel.publish(Event(event_type="task.created", actor_id="human", payload={"goal": "Explain the core idea behind Chimera"}))
    kernel.run()
    return kernel
