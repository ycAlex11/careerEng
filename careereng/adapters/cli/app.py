"""CLI command-group router with lightweight runtime-host loading."""

from __future__ import annotations

import sys
from importlib import import_module
from typing import Sequence


_LAZY_COMMAND_GROUPS = {
    "runtime-host": ("careereng.adapters.cli.runtime_commands", "runtime_host_app", True),
    "onboard": ("careereng.adapters.cli.project_commands", "project_app", False),
    "taskboard": ("careereng.adapters.cli.project_commands", "project_app", False),
    "metrics": ("careereng.adapters.cli.project_commands", "project_app", False),
    "cleanup": ("careereng.adapters.cli.project_commands", "project_app", False),
    "route": ("careereng.adapters.cli.project_commands", "project_app", False),
    "resume": ("careereng.adapters.cli.profile_commands", "profile_app", False),
    "profile": ("careereng.adapters.cli.profile_commands", "profile_app", True),
    "career-memory": ("careereng.adapters.cli.profile_commands", "profile_app", False),
    "interview": ("careereng.adapters.cli.interview_commands", "interview_app", True),
    "capture": ("careereng.adapters.cli.interview_commands", "capture_app", True),
    "assistant": ("careereng.adapters.cli.agent_commands", "agent_app", False),
    "agent-bridge": ("careereng.adapters.cli.agent_commands", "agent_app", False),
    "browser-handoff": ("careereng.adapters.cli.agent_commands", "agent_app", False),
    "mcp-server": ("careereng.adapters.cli.agent_commands", "agent_app", False),
    "action-card": ("careereng.adapters.cli.evolution_commands", "evolution_cli_app", False),
    "evolution": ("careereng.adapters.cli.evolution_commands", "evolution_cli_app", True),
    "application-summary": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "report": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "viewreport": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "site": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "batch-list": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "batch-clear": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "batch-stop": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "batch-apply": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "batch-debug-create": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "jobs": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
    "run": ("careereng.adapters.cli.application_commands", "application_cli_app", False),
}


def run(argv: Sequence[str] | None = None) -> None:
    """Dispatch a command group without importing unrelated CLI implementations."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments else ""
    lazy_target = _LAZY_COMMAND_GROUPS.get(command)
    if lazy_target:
        module_name, app_name, strip_group = lazy_target
        command_app = getattr(import_module(module_name), app_name)
        command_args = arguments[1:] if strip_group else arguments
        program_name = f"careereng {command}" if strip_group else "careereng"
        command_app(args=command_args, prog_name=program_name)
        return
    from .commands import app

    app(args=arguments, prog_name="careereng")


def __getattr__(name: str):
    """Keep ``from careereng.adapters.cli.app import app`` compatible during migration."""

    if name == "app":
        from .commands import app

        return app
    raise AttributeError(name)


__all__ = ["run", "app"]
