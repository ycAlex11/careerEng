"""CLI command helpers for external agents calling CareerEng state tools."""

from __future__ import annotations


def state_tool_command(site_key: str, tool_name: str = "<tool_name>", args: str = "<json_args>", *, phase: str = "") -> str:
    phase_arg = f" --phase {phase}" if phase else ""
    return (
        "python -m careereng agent-bridge state-call "
        f"--site {site_key}{phase_arg} --tool {tool_name} --args '{args}'"
    )


def state_tools_command(site_key: str, *, phase: str = "") -> str:
    phase_arg = f" --phase {phase}" if phase else ""
    return f"python -m careereng agent-bridge state-tools --site {site_key}{phase_arg}"


def phase_result_command(site_key: str, *, status: str = "<done|waiting_user|blocked>", summary: str = "<summary>", phase: str = "") -> str:
    phase_arg = f" --phase {phase}" if phase else ""
    return (
        "python -m careereng agent-bridge phase-result "
        f"--site {site_key}{phase_arg} --status {status} --summary '{summary}'"
    )


def state_tool_commands(site_key: str, *, phase: str = "") -> dict[str, str]:
    return {
        "tools": state_tools_command(site_key, phase=phase),
        "call": state_tool_command(site_key, phase=phase),
        "phase_result": phase_result_command(site_key, phase=phase),
    }
