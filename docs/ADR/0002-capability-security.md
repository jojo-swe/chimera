# ADR 0002: Deny-by-default capability security

- Status: Accepted
- Date: 2026-07-18

## Context

Prompt instructions are not a security boundary. An autonomous worker must not decide whether it is authorized to access a tool, secret, filesystem, network, or repository.

## Decision

The runtime checks explicit capabilities at every protected boundary. Workers receive no capabilities by default, and tool handlers are reachable only through the guarded registry.

## Consequences

Compromised workers remain constrained, permissions are inspectable and revocable, least privilege becomes the default, and policy mistakes fail closed. Integrations must define their capabilities explicitly.
