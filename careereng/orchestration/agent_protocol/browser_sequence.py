"""Agent-visible contract for generic ordered browser actions."""

from __future__ import annotations

from typing import Any


BROWSER_SEQUENCE_TOOL = "browser_sequence"
BROWSER_SEQUENCE_PHASES = frozenset({"channel_discovery", "job_retrieval", "job_filtering", "apply"})


def browser_sequence_tool_schema() -> dict[str, Any]:
    """Declare an explicit action list; runtime performs no website judgment."""

    return {
        "type": "function",
        "name": BROWSER_SEQUENCE_TOOL,
        "description": (
            "Execute an explicit ordered list of available browser tool calls on the current live page. "
            "Use only when the current page is stable and every action is already justified by live evidence and active Skills. "
            "The runtime executes in order and stops at the first tool error; it does not infer page meaning or terminal status."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool_name": {"type": "string"},
                            "arguments": {"type": "object", "additionalProperties": True},
                        },
                        "required": ["tool_name", "arguments"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["steps"],
            "additionalProperties": False,
        },
    }
