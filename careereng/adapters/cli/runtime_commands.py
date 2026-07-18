"""Thin CLI adapter for user-owned CareerEng runtime-host operations."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from careereng.config.loader import load_config
from careereng.platform.runtime_host import runtime_host_client, runtime_host_socket_path, runtime_host_status, serve_runtime_host


runtime_host_app = typer.Typer(help="User-owned local browser/runtime host commands")


def _project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "careereng").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def _workspace_path(project_root: Path) -> Path:
    workspace = load_config(project_root).paths.workspace_path(project_root)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _resolve_paths(*, project_root: str, workspace: str) -> tuple[Path, Path]:
    root = Path(project_root).expanduser().resolve() if project_root.strip() else _project_root()
    resolved_workspace = Path(workspace).expanduser().resolve() if workspace.strip() else _workspace_path(root)
    return root, resolved_workspace


@runtime_host_app.command("serve")
def runtime_host_serve(
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to the current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
    socket_path: str = typer.Option("", "--socket-path", help="Optional Unix socket path for this host"),
):
    """Run the user-owned local runtime host for browser and phase execution."""

    root, resolved_workspace = _resolve_paths(project_root=project_root, workspace=workspace)
    endpoint = Path(socket_path).expanduser() if socket_path.strip() else runtime_host_socket_path(resolved_workspace)
    serve_runtime_host(project_root=root, workspace=resolved_workspace, socket_path=endpoint)


@runtime_host_app.command("status")
def runtime_host_show_status(
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to the current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
):
    """Show runtime-host reachability without starting a host process."""

    root, resolved_workspace = _resolve_paths(project_root=project_root, workspace=workspace)
    typer.echo(json.dumps(runtime_host_status(project_root=root, workspace=resolved_workspace), ensure_ascii=False, indent=2))


@runtime_host_app.command("stop")
def runtime_host_stop(
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to the current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
    cancel_open_batches: bool = typer.Option(False, "--cancel-open-batches", help="Also cancel open batches before stopping"),
):
    """Stop the user-owned local runtime host."""

    root, resolved_workspace = _resolve_paths(project_root=project_root, workspace=workspace)
    response = runtime_host_client(project_root=root, workspace=resolved_workspace, autostart=False).shutdown(
        cancel_open_batches=cancel_open_batches,
    )
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@runtime_host_app.command("release-site")
def runtime_host_release_site(
    site_key: str = typer.Option(..., "--site", help="Registered site key whose retained runtime should be released"),
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to the current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
):
    """Release one retained site browser/profile without stopping other sites."""

    root, resolved_workspace = _resolve_paths(project_root=project_root, workspace=workspace)
    response = runtime_host_client(project_root=root, workspace=resolved_workspace, autostart=False).release_site(
        site_key=site_key,
    )
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


def serve_legacy_manager(*, project_root: str, workspace: str, socket_path: str) -> None:
    """Compatibility implementation for the hidden legacy manager command."""

    serve_runtime_host(
        project_root=Path(project_root).expanduser().resolve(),
        workspace=Path(workspace).expanduser().resolve(),
        socket_path=Path(socket_path).expanduser(),
    )
