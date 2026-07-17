# Chimera Architecture

## System boundary

Chimera is a control plane and runtime for autonomous workers. It owns worker identity and lifecycle, scheduling, authorization, budgets, tool invocation, event persistence, audit, and replay. Model reasoning remains replaceable behind worker adapters.

## Kernel invariants

1. Every event has an immutable envelope.
2. Every derived event preserves correlation and causation.
3. The event store is append-only.
4. Capability checks occur at the tool boundary.
5. Runtime budgets are charged outside worker reasoning.
6. Illegal process transitions fail closed.
7. Workers cannot emit events into a different trace.
8. Plugins cannot bypass the guarded registry.

## Process model

```text
CREATED → READY ↔ RUNNING → WAITING
             ↘       ↓         ↙
              PAUSED
                ↓
              READY

RUNNING → COMPLETED
RUNNING → FAILED
ANY ACTIVE STATE → TERMINATED
```

## Event model

Events carry a unique ID, namespaced type, actor, UTC timestamp, immutable payload, correlation ID, and causation ID. This single execution history supports audit, debugging, visual timelines, replay, and recovery.

## Capability model

Capabilities are exact resource/action pairs such as `tool:github/write`, `tool:web-search/execute`, or `secret:prod-db/reference`. The kernel denies access unless a grant exists.

## Threat model

Workers may be buggy, manipulated, or adversarial. Primary threats include prompt injection, accidental secret exposure, runaway loops, confused-deputy attacks, forged audit history, plugin supply-chain compromise, and cross-tenant leakage.
