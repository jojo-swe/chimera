from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from chimera.events import Event
from chimera.plugins.registry import ToolRegistry
from chimera.runtime.process import Process
from chimera.security import Policy


@dataclass(frozen=True, slots=True)
class AgentContext:
    process: Process
    policy: Policy
    tools: ToolRegistry


class Agent(Protocol):
    agent_type: str
    subscriptions: frozenset[str]

    def handle(self, event: Event, context: AgentContext) -> tuple[Event, ...]:
        ...
