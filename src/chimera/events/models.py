from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4


def _copy_value(value: Any) -> Any:
    # Mapping proxies cannot be deep-copied directly. Normalize nested mappings,
    # retaining the in-memory backend's support for arbitrary payload values.
    if isinstance(value, Mapping):
        return {key: _copy_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_value(item) for item in value)
    return deepcopy(value)


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

    def snapshot(self) -> Event:
        """Copy nested payload values without changing identity or trace metadata."""
        return replace(self, payload=_copy_value(self.payload))

    def child(
        self, *, event_type: str, actor_id: str, payload: Mapping[str, Any] | None = None,
    ) -> Event:
        return Event(
            event_type=event_type,
            actor_id=actor_id,
            payload=payload or {},
            correlation_id=self.correlation_id,
            causation_id=self.event_id,
        )
