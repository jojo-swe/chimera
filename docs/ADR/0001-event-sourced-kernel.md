# ADR 0001: Use an event-sourced kernel

- Status: Accepted
- Date: 2026-07-18

## Context

Autonomous systems are difficult to inspect when work occurs through nested, implicit calls. Security review, replay, visualization, and crash recovery require an explicit execution history.

## Decision

All meaningful runtime communication and lifecycle changes are represented as immutable, append-only events with correlation and causation metadata.

## Consequences

This provides a complete audit history, deterministic tests, replay, recovery, and loosely coupled workers. It also requires schema discipline, event versioning, and deliberate handling of delivery state and eventual consistency.
