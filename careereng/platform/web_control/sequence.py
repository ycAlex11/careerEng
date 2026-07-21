"""Generic execution of an LLM-supplied browser action sequence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from careereng.orchestration.agent_protocol.browser_sequence import BROWSER_SEQUENCE_TOOL
from careereng.orchestration.agent_protocol.state_tools import STATE_TOOL_NAMES


async def execute_browser_sequence(
    *,
    steps: Any,
    call_browser_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Execute explicit browser calls in order without interpreting their meaning."""

    if not isinstance(steps, list) or not steps:
        return {"isError": True, "error": "browser_sequence requires at least one step", "steps": []}
    results: list[dict[str, Any]] = []
    for index, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, dict):
            return {"isError": True, "error": f"browser_sequence step {index} must be an object", "steps": results}
        tool_name = str(raw_step.get("tool_name") or "").strip()
        arguments = raw_step.get("arguments")
        if not tool_name or tool_name == BROWSER_SEQUENCE_TOOL or tool_name in STATE_TOOL_NAMES:
            return {"isError": True, "error": f"browser_sequence step {index} has an invalid browser tool", "steps": results}
        if not isinstance(arguments, dict):
            return {"isError": True, "error": f"browser_sequence step {index} arguments must be an object", "steps": results}
        payload = await call_browser_tool(tool_name, arguments)
        result = {
            "index": index,
            "tool_name": tool_name,
            "arguments": arguments,
            "is_error": bool(payload.get("isError")) if isinstance(payload, dict) else True,
            "payload": payload if isinstance(payload, dict) else {"isError": True, "error": "invalid browser payload"},
        }
        results.append(result)
        if result["is_error"]:
            return {
                "isError": True,
                "error": f"browser_sequence stopped at step {index}: {tool_name}",
                "steps": results,
                "last_payload": result["payload"],
            }
    return {
        "isError": False,
        "steps": results,
        "completed": len(results),
        "last_payload": results[-1]["payload"] if results else {},
    }
