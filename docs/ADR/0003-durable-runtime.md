# ADR 0003: Atomic delivery records and SQLite recovery

- Status: Accepted
- Date: 2026-10-16

## Context

Persisting domain events alone cannot distinguish a queued input from an input
already handled by some or all subscribers. Re-running the entire history would
repeat committed output and tool effects, reset budgets, and lose terminal state.
The durable runtime needs a side-effect-free projection and an explicit boundary
between an interrupted attempt and committed delivery.

## Decision

Introduce an `EventStore` protocol shared by the in-memory and SQLite stores. Both
implement stable zero-based offsets, ordered reads, correlation queries, unique
event IDs, and atomic `append_many`. SQLite uses the standard library driver, WAL,
FULL synchronous commits, a correlation index, and no-update/no-delete triggers.
Its payload codec accepts JSON-compatible values rather than pickle or lossy
stringification. The entire batch is validated before the transaction starts.

Use ordinary typed `Event` envelopes for version-1 kernel journal records:

| Record | Durable fact |
| --- | --- |
| `process.registered` | Identity, type, parent, subscriptions, initial state and budget |
| `runtime.event_dispatched` | Input ID and ordered recipient identities, including an empty list |
| `runtime.delivery_started` | Input ID, pre-attempt runnable state and process snapshot |
| `runtime.budget_updated` | Budget limits and counters after a charge, before execution proceeds |
| `runtime.delivery_completed` | Input ID, outcome, final process snapshot and acknowledgement |
| `runtime.process_updated` | Explicit lifecycle changes made outside a handler |

For each recipient, append the completion record **and every emitted event in one
transaction**. Failed handlers commit a FAILED snapshot and a correlated
`process.failed` event in the same way. Reserve control event names so workers and
callers cannot publish a forged acknowledgement through the kernel. Control
records have separate traces and explicit `input_event_id` links; domain correlation
queries still contain the original domain flow.

`History.replay` folds the log without executing code or writing events. It restores
registrations, subscriptions, state, budget counters and limits, recipient lists,
and completion keys. An uncommitted attempt returns to its pre-attempt READY or
WAITING state while retaining all durable charges. Events without a dispatch record
and recipients without a completion key remain pending. Empty dispatched events
and completed recipient keys do not become work again.

`Kernel` projects history at construction. Callers bind worker implementations with
`attach_agent`, register tools, and grant capabilities explicitly before resuming.
Persisted identity is not persisted executable code or authorization. A newly
constructed kernel retains deny-by-default policy. `replay()` on an existing kernel
retains its explicitly configured policy/tools/code, but replaces process objects.

## Consequences

- Committed output is not duplicated after a crash; pending fanout resumes per
  recipient rather than re-running all subscribers.
- A handler interrupted before its atomic completion boundary may run again.
  External effects remain **at-least-once** and need idempotent tools, for example
  keyed by input event ID and process ID. SQLite cannot guarantee exactly-once
  external effects.
- Failed and interrupted attempts consume budget. Reservations are persisted before
  guarded tool execution; storage failures halt the attempt rather than bypassing
  resource enforcement. An over-limit charge retains the existing budget model's
  increment-before-check semantics.
- Handler lifecycle changes are tentative until the output/receipt commit.
  External `Process.transition()` calls are journaled immediately. Direct field
  mutation is not a durable control API, and worker-private mutable state is not
  serialized.
- Any ambiguous storage failure requires a fresh projection before further work.
  Acknowledgements decide whether the operation committed, not the caller's memory.
- Replay memory/time scales with history length; snapshots/compaction are future
  work. This implementation has one synchronous kernel owner per history. Store
  connections can append concurrently, but scheduler leases and distributed
  ownership are not implemented.
- Legacy event-only histories have no reliable acknowledgement boundary and are
  not migrated automatically. Unknown journal versions fail closed.

## Validation

Tests exercise both backends, reopen/round-trip and atomicity, partial fanout,
commit-before/after failure injection, missing bindings and policy, terminal
failures and budget exhaustion, waiting/paused states, invalid emitted traces, and
a subprocess killed with `os._exit` after durable tool/token reservations. Replay
is checked to append nothing and execute neither handlers nor tools.
