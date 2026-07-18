"""CLI adapter for project-state and generic platform operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from careereng.config.loader import ensure_files, load_config
from careereng.orchestration.engine.router_store import RouterStore
from careereng.platform.maintenance import build_cleanup_plan, execute_cleanup_plan
from careereng.platform.observability import build_metrics_summary, save_metrics_summary
from careereng.platform.project_state import TaskboardError, TaskboardStore


project_app = typer.Typer(help="Project-state and platform commands")
metrics_app = typer.Typer(help="Metrics summary commands")
route_app = typer.Typer(help="Route feedback commands")
taskboard_app = typer.Typer(help="Current development taskboard commands")
project_app.add_typer(metrics_app, name="metrics")
project_app.add_typer(route_app, name="route")
project_app.add_typer(taskboard_app, name="taskboard")


def _project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "careereng").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def _workspace_path() -> Path:
    workspace = load_config(_project_root()).paths.workspace_path(_project_root())
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def _format_duration(milliseconds: int) -> str:
    total_seconds = max(0, int(milliseconds) // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes}m{seconds}s"
    if minutes:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


def _format_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _metrics_group_lines(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [title]
    if not rows:
        return [*lines, "- none"]
    for row in rows:
        lines.append(
            f"- {row.get('name')}: calls={_format_int(row.get('calls'))} "
            f"elapsed={_format_duration(int(row.get('elapsed_ms') or 0))} "
            f"tokens={_format_int(row.get('total_tokens'))} "
            f"unknown={_format_int(row.get('unknown_token_calls'))}"
        )
    return lines


def _ensure_project_templates(project_root: Path) -> list[dict[str, str]]:
    config_path = project_root / "config.toml"
    auth_path = project_root / "auth.json"
    config_existed = config_path.exists()
    auth_existed = auth_path.exists()
    ensure_files(project_root)
    return [
        {"path": config_path.name, "kind": "file", "status": "existing" if config_existed else "created"},
        {"path": auth_path.name, "kind": "file", "status": "existing" if auth_existed else "created"},
    ]


@project_app.command("onboard")
def onboard():
    """Create the editable workspace scaffold."""

    project_root = _project_root()
    project_rows = _ensure_project_templates(project_root)
    workspace = _workspace_path()
    from careereng.career.profile.bootstrap import bootstrap_workspace

    rows = bootstrap_workspace(workspace)
    typer.echo(f"Project templates ready at {project_root}")
    typer.echo(f"created={sum(row.get('status') == 'created' for row in project_rows)} existing={sum(row.get('status') == 'existing' for row in project_rows)}")
    for row in project_rows:
        typer.echo(f"{'+' if row.get('status') == 'created' else '='} {row.get('path')}")
    typer.echo("auth.json contains template fields only; add your own provider API keys.")
    typer.echo(f"Workspace initialized at {workspace}")
    typer.echo(f"created={sum(row.get('status') == 'created' for row in rows)} existing={sum(row.get('status') == 'existing' for row in rows)}")
    for row in rows:
        typer.echo(f"{'+' if row.get('status') == 'created' else '='} {row.get('path')}")


@taskboard_app.command("show")
def taskboard_show():
    """Show the current development taskboard."""

    typer.echo(TaskboardStore(_workspace_path()).show())


@taskboard_app.command("update")
def taskboard_update(
    input_file: Path = typer.Argument(..., help="Markdown/text file containing the taskboard update"),
    source: str = typer.Option("", "--source", help="Optional source label for the update"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Create or replace the compact current development taskboard."""

    try:
        result = TaskboardStore(_workspace_path()).update_from_file(input_file, source_name=source)
    except TaskboardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"taskboard {'created' if result.get('created') else 'updated'} id={result.get('taskboard_id')} current={result.get('current_path')}")


@taskboard_app.command("done")
def taskboard_done(
    index: int = typer.Argument(..., help="1-based checkbox item index in the current taskboard"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Mark a checkbox item in the current taskboard as done."""

    try:
        result = TaskboardStore(_workspace_path()).mark_done(index)
    except TaskboardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"taskboard item {'updated' if result.get('changed') else 'already_done'} index={result.get('index')} id={result.get('taskboard_id')}")


@taskboard_app.command("archive")
def taskboard_archive(json_output: bool = typer.Option(False, "--json", help="Print JSON output")):
    """Archive the current development taskboard."""

    try:
        result = TaskboardStore(_workspace_path()).archive()
    except TaskboardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"taskboard archived id={result.get('taskboard_id')} archive={result.get('archive_path')}")


@metrics_app.command("summary")
def metrics_summary(
    batch: str = typer.Option("", "--batch", help="Batch ID, or latest"),
    site: str = typer.Option("", "--site", help="Optional site key filter"),
    phase: str = typer.Option("", "--phase", help="Optional phase filter"),
    save: bool = typer.Option(False, "--save", help="Save JSON summary under workspace/metrics/summaries"),
):
    """Summarize recorded LLM token and timing usage."""

    workspace = _workspace_path()
    summary = build_metrics_summary(workspace=workspace, batch_id=batch, site_key=site, phase=phase)
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    filters = summary.get("filters") if isinstance(summary.get("filters"), dict) else {}
    lines = [
        "Metrics Summary", f"- source: {summary.get('source_path')}", f"- batch: {filters.get('batch_id') or 'all'}",
        f"- site: {filters.get('site_key') or 'all'}", f"- phase: {filters.get('phase') or 'all'}",
        f"- calls: {_format_int(totals.get('calls'))}", f"- ok calls: {_format_int(totals.get('ok_calls'))}",
        f"- error calls: {_format_int(totals.get('error_calls'))}", f"- elapsed: {_format_duration(int(totals.get('elapsed_ms') or 0))}",
        f"- input tokens: {_format_int(totals.get('input_tokens'))}", f"- output tokens: {_format_int(totals.get('output_tokens'))}",
        f"- total tokens: {_format_int(totals.get('total_tokens'))}", f"- unknown token calls: {_format_int(totals.get('unknown_token_calls'))}", "",
    ]
    groups = summary.get("groups") if isinstance(summary.get("groups"), dict) else {}
    for title, key in (("By Site", "site_key"), ("By Phase", "phase"), ("By Model", "model"), ("By API Type", "api_type"), ("By Status", "status")):
        lines.extend(_metrics_group_lines(title, groups.get(key) if isinstance(groups.get(key), list) else []))
        lines.append("")
    errors = summary.get("error_rows") if isinstance(summary.get("error_rows"), list) else []
    lines.append("Errors")
    lines.extend(["- none"] if not errors else [f"- {row.get('ts')} batch={row.get('batch_id') or '-'} site={row.get('site_key') or '-'} phase={row.get('phase') or '-'} error={row.get('error_type') or row.get('status') or 'error'}" for row in errors[:50]])
    if len(errors) > 50:
        lines.append(f"- ... {len(errors) - 50} more")
    if save:
        lines.extend(["", f"saved: {save_metrics_summary(summary, workspace=workspace)}"])
    typer.echo("\n".join(lines).rstrip())


@project_app.command("cleanup")
def cleanup_workspace(
    days: int = typer.Option(30, "--days", min=0, help="Delete runtime artifacts older than this many days"),
    site: str = typer.Option("", "--site", help="Optional site key to limit cleanup"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview cleanup without deleting files"),
    force: bool = typer.Option(False, "--force", help="Actually delete planned files"),
    include_profile_backups: bool = typer.Option(False, "--include-profile-backups", help="Also include browser/user_data.backup.* files; never includes browser/user_data"),
):
    """Safely clean old runtime/debug artifacts without deleting job history or login profiles."""

    workspace = _workspace_path()
    plan = build_cleanup_plan(workspace=workspace, days=days, site=site, include_profile_backups=include_profile_backups)
    typer.echo(f"cleanup candidates={len(plan.candidates)} bytes={_format_bytes(plan.total_bytes)} days={plan.days} site={site or 'all'}")
    for candidate in plan.candidates[:200]:
        try:
            path = candidate.path.relative_to(workspace)
        except ValueError:
            path = candidate.path
        typer.echo(f"- {path}\t{_format_bytes(candidate.size_bytes)}\t{candidate.reason}")
    if len(plan.candidates) > 200:
        typer.echo(f"... {len(plan.candidates) - 200} more")
    if not force:
        typer.echo("dry_run=true; pass --force to delete these files" if dry_run else "refusing to delete without --force")
        return
    result = execute_cleanup_plan(plan)
    typer.echo(f"deleted={result['deleted']} bytes={_format_bytes(result['deleted_bytes'])}")


@route_app.command("feedback")
def route_feedback(
    event_id: str = typer.Option(..., "--event-id", help="Route event ID"),
    correct: str = typer.Option(..., "--correct", help="yes/no"),
    expected_route: str = typer.Option("", "--expected-route", help="chat/search/site"),
    comment: str = typer.Option("", "--comment", help="Review note"),
    reviewer_type: str = typer.Option("human", "--reviewer-type", help="human/llm/tool"),
    reviewer_name: str = typer.Option("manual", "--reviewer-name", help="Reviewer name"),
):
    """Append feedback for one route decision."""

    store = RouterStore(_workspace_path())
    event = store.find_event(event_id)
    if not event:
        raise typer.BadParameter(f"route event not found: {event_id}")
    normalized = correct.strip().lower()
    if normalized in {"yes", "y", "true", "1"}:
        is_correct = True
    elif normalized in {"no", "n", "false", "0"}:
        is_correct = False
    else:
        raise typer.BadParameter("--correct must be yes/no")
    route = expected_route.strip().lower()
    if route and route not in {"chat", "search", "site"}:
        raise typer.BadParameter("--expected-route must be chat/search/site")
    row = store.append_feedback({"route_event_id": event_id, "session_id": str(event.get("session_id") or ""), "turn_id": str(event.get("turn_id") or ""), "is_correct": is_correct, "expected_route": route, "comment": comment, "reviewer_type": reviewer_type, "reviewer_name": reviewer_name})
    typer.echo(f"feedback saved: id={row.get('feedback_id')} event={event_id} correct={is_correct} expected={route or '-'}")
