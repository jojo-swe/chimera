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
- [ ] SQLite event store
- [ ] Crash recovery and replay
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
