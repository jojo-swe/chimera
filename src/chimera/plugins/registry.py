from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from chimera.runtime.process import Process
from chimera.security import Capability, Policy

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    handler: ToolHandler

    @property
    def capability(self) -> Capability:
        return Capability(resource=f"tool:{self.name}", action="execute")


class ToolRegistry:
    def __init__(self, policy: Policy) -> None:
        self._tools: dict[str, Tool] = {}
        self._policy = policy

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def execute(self, *, tool_name: str, process: Process, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            tool = self._tools[tool_name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {tool_name}") from exc

        self._policy.require(subject=process.process_id, capability=tool.capability)
        process.budget.charge_tool_call()
        return tool.handler(dict(arguments))
