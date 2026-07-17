from uuid import uuid4

import pytest

from chimera.events import Event, InMemoryEventStore
from chimera.plugins import Tool, ToolRegistry
from chimera.runtime import Budget, BudgetExceeded, Process, ProcessState
from chimera.security import Capability, CapabilityDenied, Policy


def test_event_requires_namespaced_type() -> None:
    with pytest.raises(ValueError):
        Event(event_type="created", actor_id="human")


def test_child_preserves_trace_relationship() -> None:
    root = Event(event_type="task.created", actor_id="human")
    child = root.child(event_type="task.started", actor_id="planner")
    assert child.correlation_id == root.correlation_id
    assert child.causation_id == root.event_id


def test_store_is_append_only() -> None:
    store = InMemoryEventStore()
    correlation = uuid4()
    first = Event(event_type="task.created", actor_id="human", correlation_id=correlation)
    second = first.child(event_type="task.started", actor_id="planner")
    store.append(first)
    store.append(second)
    assert store.by_correlation(correlation) == (first, second)


def test_policy_denies_by_default() -> None:
    policy = Policy()
    capability = Capability(resource="tool:search", action="execute")
    with pytest.raises(CapabilityDenied):
        policy.require(subject="researcher", capability=capability)


def test_tool_execution_requires_capability_and_charges_budget() -> None:
    policy = Policy()
    registry = ToolRegistry(policy)
    process = Process(process_id="researcher", agent_type="researcher")
    registry.register(Tool(name="search", handler=lambda args: {"value": args["value"]}))
    policy.grant(subject="researcher", capability=Capability(resource="tool:search", action="execute"))
    assert registry.execute(tool_name="search", process=process, arguments={"value": 7}) == {"value": 7}
    assert process.budget.tool_calls_used == 1


def test_process_rejects_invalid_transition() -> None:
    process = Process(process_id="p1", agent_type="planner")
    with pytest.raises(ValueError):
        process.transition(ProcessState.COMPLETED)


def test_budget_is_enforced() -> None:
    budget = Budget(max_steps=1)
    budget.charge_step()
    with pytest.raises(BudgetExceeded):
        budget.charge_step()
