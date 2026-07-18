"""Deprecated compatibility CLI aggregator.

New entrypoints route directly to focused ``*_commands`` adapters. This module
exists only for callers that still import ``app`` programmatically.
"""

from __future__ import annotations

import typer

from careereng.adapters.cli.agent_commands import (
    agent_bridge_app,
    assistant_app,
    browser_handoff_app,
    mcp_server,
)
from careereng.adapters.cli.application_commands import (
    application_summary_app,
    batch_apply,
    batch_clear,
    batch_debug_create,
    batch_list,
    batch_stop,
    jobs_app,
    report_app,
    run,
    site_app,
    view_report,
)
from careereng.adapters.cli.evolution_commands import action_card_app, evolution_cli_app
from careereng.adapters.cli.interview_commands import capture_app, interview_app
from careereng.adapters.cli.profile_commands import career_memory_app, profile_app, resume_app
from careereng.adapters.cli.project_commands import cleanup_workspace, metrics_app, onboard, route_app, taskboard_app
from careereng.adapters.cli.runtime_commands import runtime_host_app, serve_legacy_manager


app = typer.Typer(help="CareerEng CLI (compatibility aggregator)")
app.add_typer(action_card_app, name="action-card")
app.add_typer(agent_bridge_app, name="agent-bridge")
app.add_typer(application_summary_app, name="application-summary")
app.add_typer(assistant_app, name="assistant")
app.add_typer(browser_handoff_app, name="browser-handoff")
app.add_typer(career_memory_app, name="career-memory")
app.add_typer(capture_app, name="capture")
app.add_typer(evolution_cli_app, name="evolution")
app.add_typer(interview_app, name="interview")
app.add_typer(jobs_app, name="jobs")
app.add_typer(metrics_app, name="metrics")
app.add_typer(profile_app, name="profile")
app.add_typer(report_app, name="report")
app.add_typer(resume_app, name="resume")
app.add_typer(route_app, name="route")
app.add_typer(runtime_host_app, name="runtime-host")
app.add_typer(site_app, name="site")
app.add_typer(taskboard_app, name="taskboard")
app.command("onboard")(onboard)
app.command("cleanup")(cleanup_workspace)
app.command("mcp-server")(mcp_server)
app.command("run")(run)
app.command("batch-list")(batch_list)
app.command("batch-clear")(batch_clear)
app.command("batch-stop")(batch_stop)
app.command("batch-apply")(batch_apply)
app.command("batch-debug-create")(batch_debug_create)
app.command("viewreport")(view_report)


@app.command("manager-serve", hidden=True)
def manager_serve(
    project_root: str = typer.Option(..., "--project-root"),
    workspace: str = typer.Option(..., "--workspace"),
    socket_path: str = typer.Option(..., "--socket-path"),
) -> None:
    """Deprecated alias for ``runtime-host serve``."""
    serve_legacy_manager(project_root=project_root, workspace=workspace, socket_path=socket_path)


__all__ = ["app"]
