from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


class CapabilityDenied(PermissionError):
    """Raised when a subject attempts an action it has not been granted."""


@dataclass(frozen=True, slots=True, order=True)
class Capability:
    resource: str
    action: str

    def __post_init__(self) -> None:
        if not self.resource or not self.action:
            raise ValueError("resource and action must be non-empty")


class Policy:
    """Exact-match, deny-by-default capability policy."""

    def __init__(self) -> None:
        self._grants: dict[str, set[Capability]] = {}
        self._lock = RLock()

    def grant(self, *, subject: str, capability: Capability) -> None:
        if not subject:
            raise ValueError("subject cannot be empty")
        with self._lock:
            self._grants.setdefault(subject, set()).add(capability)

    def revoke(self, *, subject: str, capability: Capability) -> None:
        with self._lock:
            self._grants.get(subject, set()).discard(capability)

    def allows(self, *, subject: str, capability: Capability) -> bool:
        with self._lock:
            return capability in self._grants.get(subject, set())

    def require(self, *, subject: str, capability: Capability) -> None:
        if not self.allows(subject=subject, capability=capability):
            raise CapabilityDenied(
                f"subject={subject!r} lacks {capability.action!r} on {capability.resource!r}"
            )

    def capabilities_for(self, subject: str) -> frozenset[Capability]:
        with self._lock:
            return frozenset(self._grants.get(subject, set()))
