from chimera.demo import build_demo_kernel
from chimera.events import Event
from chimera.runtime import ProcessState


def test_demo_flow_is_auditable_end_to_end() -> None:
    kernel = build_demo_kernel()
    root = Event(event_type="task.created", actor_id="human", payload={"goal": "Explain Chimera"})
    kernel.publish(root)
    deliveries = kernel.run()
    correlated = kernel.events.by_correlation(root.correlation_id)
    assert deliveries == 3
    assert [event.event_type for event in correlated] == [
        "task.created",
        "research.requested",
        "research.completed",
        "task.completed",
    ]
    assert kernel.process("reviewer").state is ProcessState.COMPLETED


def test_registration_events_do_not_trigger_demo_agents() -> None:
    kernel = build_demo_kernel()
    assert kernel.run() == 0
