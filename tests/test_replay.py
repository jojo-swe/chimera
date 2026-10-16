from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from chimera.agents import AgentContext
from chimera.demo import build_demo_kernel
from chimera.events import Event, EventStore, InMemoryEventStore, SQLiteEventStore
from chimera.runtime import Budget, Kernel, ProcessState
from chimera.security import Capability


@dataclass
class Worker:
    agent_type: str = "worker"
    subscriptions: frozenset[str] = frozenset({"task.created"})
    calls: int = 0
    crash: bool = False
    fail: bool = False
    tool: bool = False

    def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
        self.calls += 1
        if self.tool:
            context.tools.execute(tool_name="search", process=context.process, arguments={})
            context.process.budget.charge_tokens(7)
        if self.crash:
            raise SystemExit("simulated process crash")
        if self.fail:
            raise RuntimeError("worker failed")
        return (event.child(event_type="task.done", actor_id=context.process.process_id),)


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterable[EventStore]:
    backend = InMemoryEventStore() if request.param == "memory" else SQLiteEventStore(
        tmp_path / "history.db"
    )
    yield backend
    if isinstance(backend, SQLiteEventStore):
        backend.close()


def root() -> Event:
    return Event(event_type="task.created", actor_id="human", payload={"goal": "Chimera"})


def test_supplied_empty_store_is_not_replaced(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    assert kernel.events is store
    kernel.publish(root())
    assert len(store) == 1


def test_replay_is_read_only_and_completed_demo_is_not_redelivered(store: EventStore) -> None:
    kernel = build_demo_kernel(event_store=store)
    event = root()
    kernel.publish(event)
    assert kernel.run() == 3
    before = store.read_from()
    recovered = Kernel(event_store=store)
    assert store.read_from() == before
    assert recovered.process("reviewer").state is ProcessState.COMPLETED
    assert recovered.process("researcher").budget.tool_calls_used == 1
    recovered.replay()
    assert store.read_from() == before
    # No implementations/tools/grants are needed for read-only projection or a
    # completed history, and no acknowledgement records are rewritten.
    assert recovered.run() == 0
    assert store.read_from() == before
    assert [e.event_type for e in store.by_correlation(event.correlation_id)] == [
        "task.created", "research.requested", "research.completed", "task.completed",
    ]


def test_reopen_pending_demo_continues_without_duplicate_registration(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    with SQLiteEventStore(path) as store:
        kernel = build_demo_kernel(event_store=store)
        event = root()
        kernel.publish(event)
        with pytest.raises(RuntimeError, match="max event deliveries"):
            kernel.run(max_deliveries=1)
    with SQLiteEventStore(path) as store:
        kernel = build_demo_kernel(event_store=store)
        assert kernel.process("planner").budget.steps_used == 1
        assert kernel.run() == 2
        assert sum(e.event_type == "process.registered" for e in store) == 3
        assert len(store.by_correlation(event.correlation_id)) == 4
        assert kernel.process("reviewer").state is ProcessState.COMPLETED


def test_partial_fanout_retries_only_the_uncommitted_subscriber(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    first, second = Worker(), Worker(crash=True)
    kernel.register_agent(process_id="a", agent=first)
    kernel.register_agent(process_id="b", agent=second, parent_process_id="a")
    event = root()
    kernel.publish(event)
    with pytest.raises(SystemExit):
        kernel.run()
    recovered = Kernel(event_store=store)
    assert recovered.process("b").state is ProcessState.READY
    assert recovered.process("b").parent_process_id == "a"
    assert recovered.process("b").budget.steps_used == 1
    second.crash = False
    # Reverse attach order must not change original delivery order.
    recovered.attach_agent(process_id="b", agent=second)
    recovered.attach_agent(process_id="a", agent=first)
    assert recovered.run() == 1
    assert (first.calls, second.calls) == (1, 2)
    assert recovered.process("b").budget.steps_used == 2
    assert [e.actor_id for e in store.by_correlation(event.correlation_id)] == ["human", "a", "b"]


def test_failed_and_exhausted_processes_are_terminal_after_replay(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    failed, exhausted = Worker(fail=True), Worker()
    kernel.register_agent(process_id="failed", agent=failed)
    kernel.register_agent(process_id="exhausted", agent=exhausted, budget=Budget(max_steps=0))
    kernel.publish(root())
    assert kernel.run() == 2
    recovered = Kernel(event_store=store)
    assert recovered.process("failed").last_error == "RuntimeError: worker failed"
    assert recovered.process("exhausted").last_error == "BudgetExceeded: step budget exceeded"
    assert recovered.process("exhausted").budget.steps_used == 1
    assert recovered.process("exhausted").budget.max_steps == 0
    assert exhausted.calls == 0
    assert all(p.state is ProcessState.FAILED for p in recovered.process_table())
    assert recovered.run() == 0


def test_recovery_does_not_infer_capability_grants(store: EventStore) -> None:
    from chimera.plugins import Tool

    kernel = Kernel(event_store=store)
    worker = Worker(tool=True)
    kernel.register_agent(process_id="p", agent=worker)
    kernel.policy.grant(
        subject="p", capability=Capability(resource="tool:search", action="execute"),
    )
    kernel.publish(root())
    recovered = Kernel(event_store=store)
    recovered.attach_agent(process_id="p", agent=worker)
    calls: list[bool] = []
    recovered.tools.register(Tool(name="search", handler=lambda args: calls.append(True) or {}))
    assert recovered.run() == 1
    assert calls == []
    assert recovered.process("p").budget.tool_calls_used == 0
    assert "CapabilityDenied" in (recovered.process("p").last_error or "")


def test_missing_or_incompatible_worker_fails_closed_without_losing_pending_work(
    store: EventStore,
) -> None:
    kernel = Kernel(event_store=store)
    kernel.register_agent(process_id="p", agent=Worker())
    kernel.publish(root())
    recovered = Kernel(event_store=store)
    with pytest.raises(ValueError, match="agent_type"):
        recovered.attach_agent(process_id="p", agent=Worker(agent_type="other"))
    with pytest.raises(ValueError, match="subscriptions"):
        recovered.attach_agent(process_id="p", agent=Worker(subscriptions=frozenset()))
    with pytest.raises(RuntimeError, match="not attached"):
        recovered.run()
    with pytest.raises(RuntimeError, match="requires replay"):
        recovered.run()
    recovered.replay()
    recovered.attach_agent(process_id="p", agent=Worker())
    assert recovered.run() == 1


class FaultStore(InMemoryEventStore):
    def __init__(self, *, after_commit: bool = False) -> None:
        super().__init__()
        self.after_commit = after_commit
        self.fail_once = True

    def append_many(self, events: Iterable[Event]) -> tuple[int, ...]:
        batch = tuple(events)
        if self.fail_once and batch and batch[0].event_type == "runtime.delivery_completed":
            self.fail_once = False
            if self.after_commit:
                super().append_many(batch)
            raise OSError("simulated storage interruption")
        return super().append_many(batch)


@pytest.mark.parametrize("after_commit", [False, True])
def test_commit_boundary_never_leaves_duplicate_or_orphaned_output(after_commit: bool) -> None:
    store = FaultStore(after_commit=after_commit)
    kernel = Kernel(event_store=store)
    worker = Worker()
    kernel.register_agent(process_id="p", agent=worker)
    event = root()
    kernel.publish(event)
    with pytest.raises(OSError):
        kernel.run()
    kernel.replay()
    assert kernel.run() == (0 if after_commit else 1)
    assert worker.calls == (1 if after_commit else 2)
    assert len(store.by_correlation(event.correlation_id)) == 2


def test_invalid_second_child_never_persists_first_child(store: EventStore) -> None:
    class BadWorker(Worker):
        def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
            return (
                event.child(event_type="task.good", actor_id="p"),
                Event(event_type="task.bad", actor_id="p"),
            )
    kernel = Kernel(event_store=store)
    kernel.register_agent(process_id="p", agent=BadWorker())
    event = root()
    kernel.publish(event)
    with pytest.raises(ValueError, match="correlation_id"):
        kernel.run()
    assert store.by_correlation(event.correlation_id) == (event,)
    assert not any(e.event_type == "runtime.delivery_completed" for e in store)


def test_reserved_records_cannot_be_published_or_emitted(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    for event_type in ("process.registered", "process.failed", "runtime.delivery_completed"):
        with pytest.raises(ValueError, match="reserved"):
            kernel.publish(Event(event_type=event_type, actor_id="kernel"))
    assert len(store) == 0


def test_dispatched_empty_events_are_not_retroactively_delivered(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    kernel.publish(root())
    assert kernel.run() == 0
    recovered = Kernel(event_store=store)
    worker = Worker()
    recovered.register_agent(process_id="late", agent=worker)
    assert recovered.run() == 0
    assert worker.calls == 0


def test_real_process_crash_keeps_tool_and_token_reservations(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    code = '''
import os, sys
from chimera.events import Event, SQLiteEventStore
from chimera.runtime import Kernel, Budget
from chimera.plugins import Tool
from chimera.security import Capability
class Worker:
    agent_type = "worker"
    subscriptions = frozenset({"task.created"})
    def handle(self, event, context):
        context.process.budget.charge_tokens(7)
        context.tools.execute(tool_name="search", process=context.process, arguments={})
        os._exit(42)
store = SQLiteEventStore(sys.argv[1])
kernel = Kernel(event_store=store)
kernel.register_agent(process_id="p", agent=Worker(), budget=Budget(max_tool_calls=1))
kernel.tools.register(Tool(name="search", handler=lambda args: {}))
kernel.policy.grant(subject="p", capability=Capability(resource="tool:search", action="execute"))
kernel.publish(Event(event_type="task.created", actor_id="human"))
kernel.run()
'''
    result = subprocess.run([sys.executable, "-c", code, str(path)], timeout=15, check=False)
    assert result.returncode == 42
    from chimera.plugins import Tool

    with SQLiteEventStore(path) as store:
        kernel = Kernel(event_store=store)
        process = kernel.process("p")
        assert (process.budget.steps_used, process.budget.tool_calls_used) == (1, 1)
        assert process.budget.tokens_used == 7
        assert process.state is ProcessState.READY
        worker = Worker(tool=True)
        kernel.attach_agent(process_id="p", agent=worker)
        calls: list[bool] = []
        kernel.tools.register(Tool(name="search", handler=lambda args: calls.append(True) or {}))
        kernel.policy.grant(
            subject="p", capability=Capability(resource="tool:search", action="execute"),
        )
        assert kernel.run() == 1
        assert calls == []  # Retry cannot bypass the original tool-call limit.
        assert process.budget.steps_used == 2
        assert process.budget.tool_calls_used == 2
        assert "BudgetExceeded" in (process.last_error or "")


def test_external_lifecycle_transitions_survive_reopen(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    process = kernel.register_agent(process_id="p", agent=Worker())
    process.transition(ProcessState.PAUSED)
    recovered = Kernel(event_store=store)
    assert recovered.process("p").state is ProcessState.PAUSED
    recovered.process("p").transition(ProcessState.READY)
    assert Kernel(event_store=store).process("p").state is ProcessState.READY


def test_waiting_worker_can_resume_after_replay(store: EventStore) -> None:
    class WaitingWorker(Worker):
        def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
            context.process.transition(ProcessState.WAITING)
            return ()
    kernel = Kernel(event_store=store)
    kernel.register_agent(process_id="p", agent=WaitingWorker())
    kernel.publish(root())
    assert kernel.run() == 1
    recovered = Kernel(event_store=store)
    assert recovered.process("p").state is ProcessState.WAITING
    recovered.attach_agent(process_id="p", agent=WaitingWorker())
    recovered.publish(root())
    assert recovered.run() == 1
    assert recovered.process("p").state is ProcessState.WAITING
    assert recovered.process("p").budget.steps_used == 2


def test_tool_cannot_execute_when_budget_checkpoint_fails() -> None:
    from chimera.plugins import Tool

    class BrokenCheckpointStore(InMemoryEventStore):
        def append(self, event: Event) -> int:
            if event.event_type == "runtime.budget_updated" and (
                event.payload["budget"]["tool_calls_used"] == 1
            ):
                raise OSError("disk full")
            return super().append(event)
    store = BrokenCheckpointStore()
    kernel = Kernel(event_store=store)
    kernel.register_agent(process_id="p", agent=Worker(tool=True))
    calls: list[bool] = []
    kernel.tools.register(Tool(name="search", handler=lambda args: calls.append(True) or {}))
    kernel.policy.grant(
        subject="p", capability=Capability(resource="tool:search", action="execute"),
    )
    kernel.publish(root())
    with pytest.raises(RuntimeError, match="persist budget"):
        kernel.run()
    assert calls == []
    assert not any(e.event_type == "runtime.delivery_completed" for e in store)
    kernel.replay()
    assert kernel.process("p").state is ProcessState.READY
    assert kernel.process("p").budget.tool_calls_used == 0
    assert kernel.process("p").budget.steps_used == 1


def test_publish_with_ambiguous_commit_requires_replay() -> None:
    class AmbiguousStore(InMemoryEventStore):
        def append(self, event: Event) -> int:
            offset = super().append(event)
            if event.event_type == "task.created":
                raise OSError("connection lost after commit")
            return offset
    store = AmbiguousStore()
    kernel = Kernel(event_store=store)
    worker = Worker()
    kernel.register_agent(process_id="p", agent=worker)
    with pytest.raises(OSError):
        kernel.publish(root())
    with pytest.raises(RuntimeError, match="requires replay"):
        kernel.run()
    kernel.replay()
    assert kernel.run() == 1
    assert worker.calls == 1


@pytest.mark.parametrize("actor,version", [("kernel", 2), ("worker", 1)])
def test_unsupported_or_untrusted_runtime_history_fails_closed(actor: str, version: int) -> None:
    store = InMemoryEventStore()
    store.append(Event(
        event_type="runtime.event_dispatched", actor_id=actor,
        payload={"schema_version": version},
    ))
    with pytest.raises(ValueError, match="unsupported or untrusted"):
        Kernel(event_store=store)


def test_negative_resource_charges_cannot_reset_durable_budget(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    budget = kernel.register_agent(process_id="p", agent=Worker()).budget
    for charge in (budget.charge_step, budget.charge_tool_call, budget.charge_tokens):
        with pytest.raises(ValueError, match="negative"):
            charge(-1)
    assert Kernel(event_store=store).process("p").budget.steps_used == 0


def test_failed_replay_cannot_leave_old_projection_runnable() -> None:
    store = InMemoryEventStore()
    kernel = Kernel(event_store=store)
    worker = Worker()
    kernel.register_agent(process_id="p", agent=worker)
    kernel.publish(root())
    store.append(Event(
        event_type="runtime.unknown", actor_id="kernel", payload={"schema_version": 1},
    ))
    with pytest.raises(ValueError, match="unknown runtime"):
        kernel.replay()
    with pytest.raises(RuntimeError, match="requires replay"):
        kernel.run()
    assert worker.calls == 0


def test_orphan_completion_is_not_accepted_as_an_acknowledgement(store: EventStore) -> None:
    from chimera.runtime.history import process_snapshot

    kernel = Kernel(event_store=store)
    process = kernel.register_agent(process_id="p", agent=Worker())
    event = root()
    kernel.publish(event)
    store.append(Event(
        event_type="runtime.event_dispatched", actor_id="kernel",
        payload={"schema_version": 1, "input_event_id": str(event.event_id), "process_ids": ["p"]},
    ))
    store.append(Event(
        event_type="runtime.delivery_completed", actor_id="kernel",
        payload={
            "schema_version": 1, "input_event_id": str(event.event_id),
            "process": process_snapshot(process), "outcome": "completed",
        },
    ))
    with pytest.raises(ValueError, match="no matching delivery start"):
        Kernel(event_store=store)


def test_mutating_read_snapshots_cannot_rewrite_budget_history(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    kernel.register_agent(process_id="p", agent=Worker(), budget=Budget(max_steps=1))
    registration = store.read_from()[0]
    registration.payload["budget"]["max_steps"] = 999
    assert Kernel(event_store=store).process("p").budget.max_steps == 1


def test_published_payload_and_subscriber_payloads_are_isolated(store: EventStore) -> None:
    seen: list[int] = []

    class Mutator(Worker):
        def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
            seen.append(event.payload["nested"]["value"])
            event.payload["nested"]["value"] = 999
            return ()
    kernel = Kernel(event_store=store)
    kernel.register_agent(process_id="a", agent=Mutator())
    kernel.register_agent(process_id="b", agent=Mutator())
    event = Event(event_type="task.created", actor_id="human", payload={"nested": {"value": 1}})
    kernel.publish(event)
    event.payload["nested"]["value"] = 5
    read = store.by_correlation(event.correlation_id)[0]
    read.payload["nested"]["value"] = 7
    assert kernel.run() == 2
    assert seen == [1, 1]
    assert store.by_correlation(event.correlation_id)[0].payload["nested"]["value"] == 1


def test_recovered_inflight_process_can_be_paused_without_replay_undoing_it(
    store: EventStore,
) -> None:
    kernel = Kernel(event_store=store)
    kernel.register_agent(process_id="p", agent=Worker(crash=True))
    kernel.publish(root())
    with pytest.raises(SystemExit):
        kernel.run()
    kernel.replay()
    kernel.process("p").transition(ProcessState.PAUSED)
    recovered = Kernel(event_store=store)
    assert recovered.process("p").state is ProcessState.PAUSED
    assert recovered.run() == 0
    assert Kernel(event_store=store).run() == 0


def test_registration_owns_independent_budget_snapshots(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    limits = Budget(max_steps=3)
    kernel.register_agent(process_id="a", agent=Worker(), budget=limits)
    kernel.register_agent(process_id="b", agent=Worker(), budget=limits)
    limits.max_steps = 999
    kernel.publish(root())
    assert kernel.run() == 2
    recovered = Kernel(event_store=store)
    assert [p.budget.steps_used for p in recovered.process_table()] == [1, 1]
    assert [p.budget.max_steps for p in recovered.process_table()] == [3, 3]


def test_multiple_crash_retries_keep_one_output_and_all_attempt_charges(store: EventStore) -> None:
    kernel = Kernel(event_store=store)
    worker = Worker(crash=True)
    kernel.register_agent(process_id="p", agent=worker)
    event = root()
    kernel.publish(event)
    for _ in range(2):
        with pytest.raises(SystemExit):
            kernel.run()
        kernel.replay()
    worker.crash = False
    assert kernel.run() == 1
    assert Kernel(event_store=store).process("p").budget.steps_used == 3
    assert len(store.by_correlation(event.correlation_id)) == 2
