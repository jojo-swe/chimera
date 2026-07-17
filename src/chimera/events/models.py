from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable event envelope used for all runtime communication."""

    event_type: str
    actor_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: UUID = field(default_factory=uuid4)
    causation_id: UUID | None = None
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.event_type or "." not in self.event_type:
            raise ValueError("event_type must be namespaced, for example 'task.created'")
        if not self.actor_id:
            raise ValueError("actor_id cannot be empty")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def child(self, *, event_type: str, actor_id: str, payload: Mapping[str, Any] | None = None) -> Event:
        return Event(
            event_type=event_type,
            actor_id=actor_id,
            payload=payload or {},
            correlation_id=self.correlation_id,
            causation_id=self.event_id,
        )
