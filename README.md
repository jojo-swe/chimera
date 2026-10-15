# Chimera

**A secure runtime for autonomous AI workers.**

Chimera treats AI agents like operating-system processes: each worker has an identity,
lifecycle, capabilities, resource budget, event stream, and auditable execution history.

This repository contains the first executable architecture slice:

- typed, append-only event bus
- agent process lifecycle
- capability-based authorization
- deterministic in-process scheduler
- resource budgets
- immutable audit records
- plugin/tool contracts
- a working planner → researcher → reviewer demo
- unit tests for the core security and lifecycle invariants

> Chimera is not a prompt framework. It is the control plane beneath agents.

## Core principles

1. **Deny by default** — workers receive no capabilities unless explicitly granted.
2. **Events over hidden calls** — work moves through typed, inspectable messages.
3. **Every action is attributable** — events carry actor, correlation, and causation IDs.
4. **Budgets are enforced by the runtime** — agents do not police themselves.
5. **Model vendors are adapters** — the kernel never depends on a specific LLM.
6. **Human approval is a first-class state** — not an afterthought in a prompt.
7. **Replayability matters** — state is derived from an append-only event history.

## Architecture

```text
                         ┌──────────────────────┐
                         │    Human / API / UI  │
                         └──────────┬───────────┘
                                    │ commands
                          ┌─────────▼──────────┐
                          │  Chimera Runtime   │
                          │ scheduler + policy │
                          └───┬──────┬──────┬─┘
                              │      │      │
                        ┌─────▼─┐ ┌──▼───┐ ┌▼────────┐
                        │Agent A│ │Agent B│ │ Agent C │
                        └───┬───┘ └──┬───┘ └────┬────┘
                            │ events │           │
                       ┌────▼────────▼───────────▼────┐
                       │       Append-only Event Bus   │
                       └────┬───────────────┬─────────┘
                            │               │
                    ┌───────▼──────┐  ┌────▼─────────┐
                    │ Audit / Replay│  │ Plugin Tools │
                    └──────────────┘  └──────────────┘
```

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

pip install -e ".[dev]"
chimera demo
pytest
```

The demo creates three workers:

- `planner` creates a research request
- `researcher` uses a capability-guarded mock search tool
- `reviewer` evaluates the result and completes the task

Every transition is printed as an auditable event.

## Durable runtime

`Kernel()` still uses the in-memory store by default. Use `SQLiteEventStore` to
persist the same typed `Event` envelopes, with stable zero-based offsets,
correlation queries, and atomic `append_many` batches. SQLite uses WAL and FULL
synchronous commits; duplicate event IDs, updates, and deletes are rejected.
Payloads must contain JSON-compatible mappings with string keys, lists, and finite
scalar values. UUIDs and timestamps in the envelope are preserved exactly.

The demo builder can also configure a fresh or recovered SQLite-backed kernel:

```python
from chimera.demo import build_demo_kernel
from chimera.events import Event, SQLiteEventStore

with SQLiteEventStore("chimera.db") as store:
    kernel = build_demo_kernel(event_store=store)
    # Re-running this example resumes existing work rather than creating it again.
    if not any(event.event_type == "task.created" for event in store):
        kernel.publish(Event(
            event_type="task.created", actor_id="human",
            payload={"goal": "Explain Chimera"},
        ))
    kernel.run()
```

### Replay and recovery guarantees

- `Kernel(event_store=store)` and `kernel.replay()` reconstruct process identity,
  lifecycle, parent relationships, budget limits/usage, and pending deliveries
  **without running workers or tools and without appending events**.
- For custom workers, use `attach_agent(process_id=..., agent=...)` to bind code to
  recovered processes. Agent type and subscriptions must match the registration.
  Restore tool implementations and explicitly grant capabilities before `run()`.
  A new kernel is always **deny-by-default**: grants are not inferred from history.
- Each event's recipient list is journaled in registration order. Each recipient's
  completion receipt, final process snapshot, and all emitted events commit in
  **one transaction**. Completed recipients are never redelivered; a partial fanout
  resumes only its unfinished recipients, and dispatched events with no recipients
  are not delivered retroactively to newly registered workers.
- Interrupted handlers are **at-least-once**: their uncommitted output is absent,
  and they are retried from their prior READY/WAITING state. External tool effects
  cannot be rolled back by SQLite. Make side-effecting tools idempotent using the
  input event ID and process ID; this is **not an exactly-once tool guarantee**.
- Step, tool-call, and token charges are committed before execution proceeds.
  Charges from interrupted attempts remain consumed; retries consume another step.
  Budget exhaustion and worker failures are durable terminal states.
- Call `Process.transition()` for lifecycle changes outside handlers; those changes
  are journaled too. Supply custom limits via `register_agent(..., budget=Budget(...))`.
  Direct assignment to mutable process/budget fields is not a durable control API.
- After an interrupted `run()` (including a delivery limit or storage error), call
  `replay()` or construct a new kernel before continuing. Fetch process references
  again after replay, since the projection rebuilds them. Do not serialize private
  mutable worker state: use stateless handlers or derive that state from events.

The `runtime.*`, `process.registered`, and `process.failed` event types are reserved
for kernel control records. Versioned journal records use separate correlation IDs
and link to domain events through `input_event_id`, keeping domain correlation
queries unchanged. Runtime records are not dispatched to workers. Recovery supports
histories written by this durable runtime, not legacy logs without delivery records.

This is a **single-owner, synchronous runtime**: SQLite supports concurrent store
connections, but only one kernel may schedule a given history at a time. Worker
leases, asynchronous execution, and human approval gates remain roadmap items.

## Example policy

```python
from chimera.security import Capability, Policy

policy = Policy()
policy.grant(
    subject="researcher",
    capability=Capability(resource="tool:web-search", action="execute"),
)
```

A worker without that exact capability receives `CapabilityDenied`.

## Package layout

```text
src/chimera/
├── agents/       Worker contracts and demo agents
├── events/       Event envelope, store, subscriptions
├── plugins/      Tool/plugin contracts and registry
├── runtime/      Scheduler, process table, budgets
├── security/     Capabilities and policy engine
└── cli.py        Executable demo
```

## Roadmap

### 0.1 — Kernel slice
- [x] Typed events
- [x] Process lifecycle
- [x] Capability policy
- [x] Budgets
- [x] Plugin registry
- [x] Deterministic demo
- [x] Core tests

### 0.2 — Durable runtime
- [x] SQLite event store
- [x] Crash recovery and replay
- [ ] Async worker execution
- [ ] Human approval gates
- [ ] Secret references
- [ ] OpenTelemetry traces

### 0.3 — Distributed runtime
- [ ] PostgreSQL event store
- [ ] Worker leases and heartbeats
- [ ] Remote execution nodes
- [ ] Sandboxed tool runners
- [ ] Web control plane
- [ ] Live topology view

## License

Apache-2.0. See [LICENSE](LICENSE).
