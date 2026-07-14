"""Browser-runtime bridge helpers for external agents."""

from __future__ import annotations


def browser_tool_command(site_key: str, tool_name: str = "<tool_name>", args: str = "<json_args>") -> str:
    return f"python -m careereng agent-bridge browser-call --site {site_key} --tool {tool_name} --args '{args}'"


def legacy_browser_tool_command(site_key: str, tool_name: str = "<tool_name>", args: str = "<json_args>") -> str:
    return f"python -m careereng browser-handoff call --site {site_key} --tool {tool_name} --args '{args}'"


def browser_tool_commands(site_key: str) -> dict[str, str]:
    """Return the preferred external-agent commands for one active site runtime."""

    return {
        "tools": f"python -m careereng agent-bridge browser-tools --site {site_key}",
        "snapshot": browser_tool_command(site_key, "browser_snapshot", "{}"),
        "call": browser_tool_command(site_key),
        "legacy_tools": f"python -m careereng browser-handoff tools --site {site_key}",
        "legacy_snapshot": legacy_browser_tool_command(site_key, "browser_snapshot", "{}"),
        "legacy_call": legacy_browser_tool_command(site_key),
    }
