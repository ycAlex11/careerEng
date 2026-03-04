"""Minimal tool registry."""

from __future__ import annotations

from typing import Any, Callable


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable[..., dict[str, Any]]] = {}

    def register(self, name: str, fn: Callable[..., dict[str, Any]]) -> None:
        self._tools[name] = fn

    def execute(self, name: str, **kwargs: Any) -> dict[str, Any]:
        fn = self._tools.get(name)
        if fn is None:
            raise KeyError(f"Unknown tool: {name}")
        return fn(**kwargs)

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())
