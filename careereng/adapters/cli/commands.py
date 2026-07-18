"""CLI commands."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import typer
import yaml
from careereng.evolution.work_items import ActionCardError, ActionCardStore
from careereng.career.applications import (
    build_application_summary,
    inspect_history_repairs,
    save_application_summary,
    save_history_repair_plan,
)
from careereng.career.memory import (
    CareerMemoryError,
    import_memory_candidates,
    list_memory_units,
    promote_assistant_signals,
    show_memory_unit,
)
from careereng.career.interviews.capture import AudioCaptureDependencyError, capture_audio_chunks, list_audio_devices
from careereng.platform.maintenance import build_cleanup_plan, execute_cleanup_plan
from careereng.config.loader import ensure_files
from careereng.adapters.bootstrap import build_loop as runtime_build_loop
from careereng.adapters.bootstrap import build_site_services as runtime_build_site_services
from careereng.adapters.bootstrap import project_root_from_cwd, workspace_path as runtime_workspace_path
from careereng.career.profile.bootstrap import bootstrap_workspace
from careereng.adapters.host.workspace_manager import (
    call_agent_bridge_browser_tool,
    call_agent_bridge_state_tool,
    call_browser_handoff_tool,
    dispatch_manager_message,
    list_agent_bridge_browser_tools,
    list_agent_bridge_state_tools,
    list_browser_handoff_tools,
    serve_workspace_manager,
    shutdown_workspace_manager,
    start_manager_jobs_batch,
)
from careereng.evolution import (
    CandidateSpecError,
    EvolutionApplyError,
    EvolutionEvaluationError,
    EvolutionProposalError,
    EvolutionRollbackError,
    EvolutionSolutionError,
    EvolutionTriggerError,
    apply_evolution_run,
    build_evolution_review,
    create_evolution_run,
    create_solution_request_for_action_card,
    create_solution_request_for_run,
    evaluate_evolution_run,
    get_candidate_spec,
    list_pending_solution_requests,
    load_candidate_specs,
    rollback_evolution_run,
    save_evolution_review,
    scan_evolution_triggers,
)
from careereng.evolution.browser_control.lessons import BrowserControlLessonStore, render_lessons_markdown
from careereng.adapters.external_agents.browser import browser_tool_command, legacy_browser_tool_command
from careereng.adapters.external_agents.contracts import AGENT_BRIDGE_STATUS, is_agent_bridge_reason
from careereng.adapters.external_agents.state import phase_result_command, state_tool_command, state_tools_command
from careereng.evolution.outer_loop import BatchEvolutionOrchestrator
from careereng.adapters.assistant_bridge.context import build_assistant_context_pack
from careereng.adapters.assistant_bridge import AssistantThreadStateStore, ingest_assistant_message
from careereng.adapters.assistant_bridge.intake_state import save_recent_intake_state
from careereng.career.interviews import (
    InterviewStore,
    InterviewStoreError,
    build_interview_summary,
    render_interview_summary,
    save_interview_candidates,
)
from careereng.platform.observability import build_metrics_summary, save_metrics_summary
from careereng.platform.runtime_host import runtime_host_client, runtime_host_socket_path, runtime_host_status, serve_runtime_host
from careereng.adapters.mcp import run_mcp_server
from careereng.career.resume.export import ResumeExportError, export_resume_pdf as export_resume_pdf_file
from careereng.career.applications import generate_job_batch_report
from careereng.career.applications.job_store import JobStore
from careereng.platform.persistence import JSONLStore
from careereng.career.profile.intent_store import IntentStore
from careereng.career.profile.store import ProfileStore
from careereng.orchestration.engine.router_store import RouterStore
from careereng.platform.project_state import TaskboardError, TaskboardStore
from careereng.career.applications.site_bootstrap import bootstrap_site as bootstrap_site_launcher
from careereng.career.applications.batch_debug import BatchApplyDebugRunner
from careereng.utils import make_id, safe_file_stem

app = typer.Typer(help="CareerEng CLI")
action_card_app = typer.Typer(help="Action card review tasks")
agent_bridge_app = typer.Typer(help="External agent bridge commands")
application_summary_app = typer.Typer(help="Application lifecycle summary commands")
assistant_app = typer.Typer(help="External AI assistant bridge commands")
browser_handoff_app = typer.Typer(help="Codex/external-agent browser handoff commands")
career_memory_app = typer.Typer(help="Career memory commands")
capture_app = typer.Typer(help="Local capture commands")
capture_audio_app = typer.Typer(help="Audio capture commands")
evolution_app = typer.Typer(help="Evolution review commands")
jobs_app = typer.Typer(help="Registered-site job retrieval/apply commands")
interview_app = typer.Typer(help="Interview preparation and transcript records")
profile_app = typer.Typer(help="Profile/persona commands")
metrics_app = typer.Typer(help="Metrics summary commands")
report_app = typer.Typer(help="Report review commands")
resume_app = typer.Typer(help="Resume commands")
route_app = typer.Typer(help="Route feedback commands")
runtime_host_app = typer.Typer(help="User-owned local browser/runtime host commands")
site_app = typer.Typer(help="Site registry commands")
taskboard_app = typer.Typer(help="Current development taskboard commands")
app.add_typer(action_card_app, name="action-card")
app.add_typer(agent_bridge_app, name="agent-bridge")
app.add_typer(application_summary_app, name="application-summary")
app.add_typer(assistant_app, name="assistant")
app.add_typer(browser_handoff_app, name="browser-handoff")
app.add_typer(career_memory_app, name="career-memory")
app.add_typer(capture_app, name="capture")
capture_app.add_typer(capture_audio_app, name="audio")
app.add_typer(evolution_app, name="evolution")
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


PROFILE_GENERATE_MESSAGE = "请根据当前 workspace 中已有的简历、profile sources 和对话信息，生成或更新用户画像 persona.md。"
JOBS_APPLY_MESSAGE = "检索投递已注册的公司"
JOBS_REVIEW_STATUS_MESSAGE = "检查已投递岗位状态"
APPLICATION_SUMMARY_REPAIR_THRESHOLD = 25


def _project_root() -> Path:
    return project_root_from_cwd()


def _workspace_path() -> Path:
    return runtime_workspace_path(_project_root())


def _csv_list(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _ensure_project_templates(project_root: Path) -> list[dict[str, str]]:
    config_path = project_root / "config.toml"
    auth_path = project_root / "auth.json"
    config_existed = config_path.exists()
    auth_existed = auth_path.exists()
    ensure_files(project_root)
    return [
        {
            "path": config_path.name,
            "kind": "file",
            "status": "existing" if config_existed else "created",
        },
        {
            "path": auth_path.name,
            "kind": "file",
            "status": "existing" if auth_existed else "created",
        },
    ]


def _build_site_services() -> tuple[Path, Path, Any, Any, Any, Any, Any]:
    root = _project_root()
    workspace = _workspace_path()
    return runtime_build_site_services(project_root=root, workspace=workspace)


def _build_loop() -> tuple[Any, Any]:
    root = _project_root()
    workspace = _workspace_path()
    return runtime_build_loop(project_root=root, workspace=workspace)


def _job_store() -> JobStore:
    return JobStore(_workspace_path())


def _close_loop_if_possible(loop: Any) -> None:
    closer = getattr(loop, "close", None)
    if callable(closer):
        closer()


def _format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
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
        lines.append("- none")
        return lines
    for row in rows:
        lines.append(
            f"- {row.get('name')}: "
            f"calls={_format_int(row.get('calls'))} "
            f"elapsed={_format_duration(int(row.get('elapsed_ms') or 0))} "
            f"tokens={_format_int(row.get('total_tokens'))} "
            f"unknown={_format_int(row.get('unknown_token_calls'))}"
        )
    return lines


@application_summary_app.command("build")
def application_summary_build(
    since: str = typer.Option("2026-04-01", "--since", help="Only include application data on or after this date"),
    all_time: bool = typer.Option(False, "--all-time", help="Include all historical application data"),
):
    """Build the machine-readable application lifecycle summary."""
    root = _project_root()
    workspace = _workspace_path()
    summary = build_application_summary(workspace=workspace, project_root=root, since=None if all_time else since)
    path = save_application_summary(summary, workspace=workspace)
    source = summary.get("source") if isinstance(summary.get("source"), dict) else {}
    filters = source.get("filters") if isinstance(source.get("filters"), dict) else {}
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    typer.echo(
        "application_summary: "
        f"since={filters.get('since') or 'all'} "
        f"sites={_format_int(source.get('site_count'))} "
        f"jobs={_format_int(totals.get('history_jobs'))} "
        f"submitted={_format_int(totals.get('submitted'))} "
        f"active={_format_int(totals.get('active'))} "
        f"rejected={_format_int(totals.get('rejected'))} "
        f"transitions={_format_int(len(summary.get('lifecycle_transitions') or []))} "
        f"unmatched={_format_int(totals.get('unmatched_reviews'))} "
        f"legacy_unmatched={_format_int(totals.get('legacy_unmatched_reviews'))} "
        f"rematched={_format_int(totals.get('rematched_reviews'))} "
        f"signals={_format_int(len(summary.get('signals') or []))}"
    )
    typer.echo(f"path: {path}")
    legacy_unmatched = int(totals.get("legacy_unmatched_reviews") or 0)
    if legacy_unmatched >= APPLICATION_SUMMARY_REPAIR_THRESHOLD:
        typer.echo(
            "repair_recommended: "
            f"legacy_unmatched={legacy_unmatched} threshold={APPLICATION_SUMMARY_REPAIR_THRESHOLD}; "
            "run `python -m careereng application-summary repair-history` before applying safe repairs."
        )


@application_summary_app.command("repair-history")
def application_summary_repair_history(
    since: str = typer.Option("2026-04-01", "--since", help="Only inspect application data on or after this date"),
    all_time: bool = typer.Option(False, "--all-time", help="Inspect all historical application data"),
    apply_repairs: bool = typer.Option(
        False,
        "--apply",
        help=(
            "Apply only safe repairs: review-log matched_job_id backfill, missing site_job_id, "
            "missing review raw/stage, and dashboard URL anomaly markers"
        ),
    ),
):
    """Inspect history data quality and optionally apply safe repairs."""
    root = _project_root()
    workspace = _workspace_path()
    plan = inspect_history_repairs(
        workspace=workspace,
        project_root=root,
        since=None if all_time else since,
        apply=apply_repairs,
    )
    path = save_history_repair_plan(plan, workspace=workspace)
    source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
    filters = source.get("filters") if isinstance(source.get("filters"), dict) else {}
    totals = plan.get("totals") if isinstance(plan.get("totals"), dict) else {}
    category_counts = plan.get("category_counts") if isinstance(plan.get("category_counts"), dict) else {}
    typer.echo(
        "history_repair: "
        f"mode={plan.get('mode') or 'dry_run'} "
        f"since={filters.get('since') or 'all'} "
        f"issues={_format_int(totals.get('issue_count'))} "
        f"safe_repairable={_format_int(totals.get('safe_repairable_count'))} "
        f"applied={_format_int(totals.get('applied_count'))}"
    )
    for key in sorted(category_counts):
        typer.echo(f"- {key}: {_format_int(category_counts.get(key))}")
    typer.echo(f"path: {path}")


_PHASE_EVENT_LABELS = {
    "browser.phase.done": "done",
    "browser.phase.blocked": "blocked",
    "browser.phase.failed": "failed",
}


def _site_events_path(workspace: Path, site_key: str) -> Path:
    return workspace / "sites" / safe_file_stem(site_key) / "events" / "all.jsonl"


def _format_phase_progress_line(site_key: str, event: dict[str, Any]) -> str:
    name = str(event.get("name") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    status = _PHASE_EVENT_LABELS.get(name, name)
    phase = str(payload.get("phase") or "").strip()
    line = f"{site_key} {status}"
    if phase:
        line += f" {phase}"
    return line


def _emit_phase_progress(
    *,
    workspace: Path,
    session_id: str,
    baseline_batch_ids: set[str],
    state: dict[str, Any],
) -> int:
    job_store = JobStore(workspace)
    batch_id = str(state.get("batch_id") or "")
    if not batch_id:
        for row in job_store.list_batches(session_id=session_id, include_terminal=True):
            candidate = str(row.get("batch_id") or "")
            if candidate and candidate not in baseline_batch_ids:
                state["batch_id"] = candidate
                state["turn_id"] = str(row.get("turn_id") or "")
                batch_id = candidate
                break
    if not batch_id:
        return 0
    batch = job_store.load_batch(batch_id)
    turn_id = str(state.get("turn_id") or batch.get("turn_id") or "")
    if turn_id:
        state["turn_id"] = turn_id
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    seen = state.setdefault("seen_phase_events", set())
    pending: list[tuple[str, str, tuple[str, str, str, str, str]]] = []
    for site_key in sorted(sites.keys()):
        events_path = _site_events_path(workspace, site_key)
        if not events_path.exists():
            continue
        for event in JSONLStore(events_path).read_all():
            if not isinstance(event, dict):
                continue
            name = str(event.get("name") or "")
            if name not in _PHASE_EVENT_LABELS:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if turn_id and str(payload.get("turn_id") or "") != turn_id:
                continue
            key = (
                site_key,
                str(event.get("ts") or ""),
                name,
                str(payload.get("phase") or ""),
                str(payload.get("summary") or ""),
            )
            if key in seen:
                continue
            pending.append((str(event.get("ts") or ""), _format_phase_progress_line(site_key, event), key))
    pending.sort(key=lambda row: row[0])
    for _, line, key in pending:
        seen.add(key)
        typer.echo(line)
    return len(pending)


def _dispatch_message_with_progress(*, message: str, session: str) -> str:
    root = _project_root()
    workspace = _workspace_path()
    baseline_batch_ids = {
        str(row.get("batch_id") or "")
        for row in JobStore(workspace).list_batches(session_id=session, include_terminal=True)
        if str(row.get("batch_id") or "")
    }
    progress_state: dict[str, Any] = {}
    result: dict[str, Any] = {"reply": "", "error": None}

    def _worker() -> None:
        try:
            result["reply"] = dispatch_manager_message(
                project_root=root,
                workspace=workspace,
                session_id=session,
                message=message,
            )
        except BaseException as exc:  # pragma: no cover - exercised via CLI behavior
            result["error"] = exc

    worker = threading.Thread(target=_worker, name="careereng-cli-run", daemon=True)
    worker.start()
    while worker.is_alive():
        _emit_phase_progress(
            workspace=workspace,
            session_id=session,
            baseline_batch_ids=baseline_batch_ids,
            state=progress_state,
        )
        worker.join(timeout=0.75)
    _emit_phase_progress(
        workspace=workspace,
        session_id=session,
        baseline_batch_ids=baseline_batch_ids,
        state=progress_state,
    )
    error = result.get("error")
    if isinstance(error, BaseException):
        raise error
    return str(result.get("reply") or "")


_BATCH_MONITOR_DONE_STATUSES = {
    "completed",
    "partial_completed",
    "failed",
    "cancelled",
    "waiting_solution",
    "waiting_user",
}
_BATCH_MONITOR_HEARTBEAT_SECONDS = 60.0


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remainder = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{remainder}s"
    return f"{remainder}s"


def _batch_report_paths(workspace: Path, batch: dict[str, Any]) -> tuple[Path | None, Path | None]:
    batch_id = str(batch.get("batch_id") or "")
    report_date = str(batch.get("created_at") or "")[:10]
    if not batch_id or not report_date:
        return None, None
    report_dir = workspace / "reports" / "jobs" / report_date
    return report_dir / f"{batch_id}.md", report_dir / "final.md"


def _format_active_batch_work(batch: dict[str, Any]) -> str:
    operation = str(batch.get("operation") or "job_search")
    apply_requested = bool(batch.get("apply_requested"))
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    active: list[str] = []
    for site_key in sorted(sites.keys()):
        row = sites.get(site_key)
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        retrieve = row.get("retrieve") if isinstance(row.get("retrieve"), dict) else {}
        apply = row.get("apply") if isinstance(row.get("apply"), dict) else {}
        retrieve_status = str(retrieve.get("status") or "")
        apply_status = str(apply.get("status") or "")
        if (
            operation == "job_search"
            and apply_requested
            and status not in {"blocked_login", "blocked", "waiting_solution", "failed", "skipped"}
            and (apply_status == "running" or (apply_status == "pending" and retrieve_status == "done"))
        ):
            active.append(f"{site_key}:apply")
            continue
        if status in {"queued", "running", "ready"}:
            active.append(f"{site_key}:{row.get('current_phase') or status}")
            continue
        if status in {"blocked_login", "blocked", "waiting_solution"}:
            active.append(f"{site_key}:blocked")
    return ", ".join(active) if active else "none"


def _format_batch_heartbeat(*, batch: dict[str, Any], workspace: Path, elapsed_seconds: float) -> str:
    batch_id = str(batch.get("batch_id") or "")
    report_path, _final_report_path = _batch_report_paths(workspace, batch)
    parts = [
        f"still running batch={batch_id}",
        f"elapsed={_format_elapsed(elapsed_seconds)}",
        f"active={_format_active_batch_work(batch)}",
    ]
    if report_path:
        parts.append(f"report={report_path}")
    return " ".join(parts)


def _format_monitored_batch_summary(batch: dict[str, Any], *, workspace: Path | None = None) -> str:
    batch_id = str(batch.get("batch_id") or "")
    status = str(batch.get("status") or "unknown")
    operation = str(batch.get("operation") or "job_search")
    lines = [f"batch={batch_id} status={status} operation={operation}"]
    if workspace is not None:
        report_path, final_report_path = _batch_report_paths(workspace, batch)
        if report_path:
            lines.append(f"report={report_path}")
        if final_report_path:
            lines.append(f"final_report={final_report_path}")
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    for site_key in sorted(sites.keys()):
        row = sites.get(site_key)
        if not isinstance(row, dict):
            continue
        retrieve = row.get("retrieve") if isinstance(row.get("retrieve"), dict) else {}
        apply = row.get("apply") if isinstance(row.get("apply"), dict) else {}
        parts = [
            f"- {row.get('site_name') or site_key} [{site_key}]",
            f"status={row.get('status') or 'unknown'}",
            f"phase={row.get('current_phase') or ''}",
            f"retrieve={retrieve.get('status') or 'skipped'}",
            f"apply={apply.get('status') or 'skipped'}",
        ]
        reason = str(row.get("reason_tag") or apply.get("reason_tag") or retrieve.get("reason_tag") or "")
        if reason:
            parts.append(f"reason={reason}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _format_pending_solution_handoff(*, workspace: Path, batch_id: str) -> str:
    rows = list_pending_solution_requests(workspace=workspace, batch_id=batch_id, limit=1)
    if not rows:
        return "next_action=no_pending_solution_found"
    row = rows[0]
    lines = [
        f"next_action={row.get('next_action') or 'write_proposal'}",
        f"run={row.get('run_id') or ''}",
        f"solution_request={row.get('solution_request') or ''}",
        f"proposal_output={row.get('proposal_output_path') or ''}",
        f"apply_command={row.get('apply_command') or ''}",
        f"continue_command=python -m careereng evolution continue-batch --batch {batch_id}",
    ]
    return "\n".join(lines)


def _message_field(message: str, key: str) -> str:
    marker = f"{key}="
    if marker not in message:
        return ""
    tail = message.split(marker, 1)[1]
    return tail.split()[0].strip()


def _format_pending_agent_bridge(batch: dict[str, Any]) -> str:
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    for site_key in sorted(sites.keys()):
        row = sites.get(site_key)
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason_tag") or "").strip()
        if not is_agent_bridge_reason(reason):
            continue
        message = str(row.get("message") or "")
        work_order = _message_field(message, "work_order")
        payload = _message_field(message, "payload")
        phase = str(row.get("current_phase") or "").strip()
        lines = [
            "next_action=agent_bridge_browser",
            f"site={site_key}",
            f"phase={phase}",
            f"browser_tools_command=python -m careereng agent-bridge browser-tools --site {site_key}",
            f"browser_snapshot_command={browser_tool_command(site_key, 'browser_snapshot', '{}')}",
            f"state_tools_command={state_tools_command(site_key, phase=phase)}",
            f"state_call_command={state_tool_command(site_key, phase=phase)}",
            f"phase_result_command={phase_result_command(site_key, phase=phase)}",
            f"legacy_browser_tools_command=python -m careereng browser-handoff tools --site {site_key}",
            f"legacy_browser_snapshot_command={legacy_browser_tool_command(site_key, 'browser_snapshot', '{}')}",
        ]
        if work_order:
            lines.append(f"browser_work_order={work_order}")
        if payload:
            lines.append(f"browser_payload={payload}")
        return "\n".join(lines)
    return ""


def _format_pending_browser_handoff(batch: dict[str, Any]) -> str:
    return _format_pending_agent_bridge(batch)


def _next_evolution_batch_id(batch: dict[str, Any]) -> str:
    payload = batch.get("evolution_loop") if isinstance(batch.get("evolution_loop"), dict) else {}
    next_batch_id = str(payload.get("next_batch_id") or "").strip()
    current_batch_id = str(batch.get("batch_id") or "").strip()
    return next_batch_id if next_batch_id and next_batch_id != current_batch_id else ""


def _shutdown_manager_after_terminal_batch(*, root: Path, workspace: Path) -> str:
    try:
        response = shutdown_workspace_manager(
            project_root=root,
            workspace=workspace,
            cancel_open_batches=False,
            wait_timeout_seconds=10.0,
        )
    except Exception as exc:
        return f"manager_shutdown=failed error={exc}"
    if not bool(response.get("running")):
        return "manager=not_running"
    if bool(response.get("stopped")):
        return "manager=stopped"
    return "manager_shutdown=pending"


def _workspace_browser_profile_dirs(workspace: Path) -> list[Path]:
    sites_dir = Path(workspace) / "sites"
    if not sites_dir.exists():
        return []
    return sorted(path.resolve() for path in sites_dir.glob("*/browser/user_data") if path.exists())


def _list_workspace_browser_pids(workspace: Path) -> list[int]:
    markers = [str(path) for path in _workspace_browser_profile_dirs(workspace)]
    if not markers:
        return []
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    current_pid = os.getpid()
    pids: set[int] = set()
    for line in str(proc.stdout or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        pid_text, _, command = raw.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if any(marker in command for marker in markers):
            pids.add(pid)
    return sorted(pids)


def _list_careereng_long_task_pids() -> list[int]:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    current_pid = os.getpid()
    pids: set[int] = set()
    long_task_markers = (
        "careereng jobs apply",
        "careereng jobs review-status",
        "-m careereng jobs apply",
        "-m careereng jobs review-status",
    )
    for line in str(proc.stdout or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        pid_text, _, command = raw.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if any(marker in command for marker in long_task_markers):
            pids.add(pid)
    return sorted(pids)


def _list_workspace_manager_pids(*, project_root: Path, workspace: Path) -> list[int]:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    current_pid = os.getpid()
    root_marker = str(Path(project_root).resolve())
    workspace_marker = str(Path(workspace).resolve())
    pids: set[int] = set()
    for line in str(proc.stdout or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        pid_text, _, command = raw.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if "manager-serve" in command and root_marker in command and workspace_marker in command:
            pids.add(pid)
    return sorted(pids)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pids_exit(pids: set[int], *, timeout_seconds: float) -> set[int]:
    deadline = time.time() + max(0.0, float(timeout_seconds or 0.0))
    alive = set(pids)
    while alive and time.time() < deadline:
        alive = {pid for pid in alive if _pid_alive(pid)}
        if alive:
            time.sleep(0.1)
    return {pid for pid in alive if _pid_alive(pid)}


def _stop_pids(pids: set[int], *, timeout_seconds: float = 3.0) -> int:
    if not pids:
        return 0
    for pid in sorted(pids):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    survivors = _wait_for_pids_exit(pids, timeout_seconds=timeout_seconds)
    for pid in sorted(survivors):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    return len(pids)


def _stop_workspace_browser_processes(workspace: Path) -> int:
    pids = set(_list_workspace_browser_pids(workspace))
    return _stop_pids(pids)


def _stop_workspace_browser_processes_until_clean(workspace: Path, *, attempts: int = 3) -> int:
    stopped: set[int] = set()
    for _ in range(max(1, attempts)):
        pids = set(_list_workspace_browser_pids(workspace))
        if not pids:
            break
        stopped.update(pids)
        _stop_pids(pids)
        time.sleep(0.2)
    return len(stopped)


def _stop_workspace_manager_processes(*, project_root: Path, workspace: Path) -> int:
    pids = set(_list_workspace_manager_pids(project_root=project_root, workspace=workspace))
    return _stop_pids(pids, timeout_seconds=2.0)


def _stop_careereng_long_task_processes() -> int:
    pids = set(_list_careereng_long_task_pids())
    return _stop_pids(pids)


def _dispatch_jobs_batch_with_monitor(
    *,
    operation: str,
    apply_requested: bool,
    message: str,
    session: str,
) -> str:
    root = _project_root()
    workspace = _workspace_path()
    job_store = JobStore(workspace)
    baseline_batch_ids = {
        str(row.get("batch_id") or "")
        for row in job_store.list_batches(session_id=session, include_terminal=True)
        if str(row.get("batch_id") or "")
    }
    response = start_manager_jobs_batch(
        project_root=root,
        workspace=workspace,
        session_id=session,
        message=message,
        operation=operation,
        apply_requested=apply_requested,
    )
    if not bool(response.get("accepted")):
        return str(response.get("reply") or "job batch was not accepted")
    batch_id = str(response.get("batch_id") or "")
    turn_id = str(response.get("turn_id") or "")
    progress_state: dict[str, Any] = {"batch_id": batch_id, "turn_id": turn_id}
    typer.echo(str(response.get("reply") or f"batch={batch_id} status=running"))
    started_at = time.monotonic()
    last_activity_at = started_at
    try:
        while True:
            emitted_count = _emit_phase_progress(
                workspace=workspace,
                session_id=session,
                baseline_batch_ids=baseline_batch_ids,
                state=progress_state,
            )
            if emitted_count:
                last_activity_at = time.monotonic()
            try:
                batch = job_store.load_batch(batch_id)
            except Exception:
                time.sleep(0.75)
                continue
            status = str(batch.get("status") or "")
            if status in _BATCH_MONITOR_DONE_STATUSES:
                _emit_phase_progress(
                    workspace=workspace,
                    session_id=session,
                    baseline_batch_ids=baseline_batch_ids,
                    state=progress_state,
                )
                summary = _format_monitored_batch_summary(batch, workspace=workspace)
                if status in {"waiting_user", "waiting_solution"}:
                    handoff = _format_pending_agent_bridge(batch)
                    if not handoff:
                        handoff = _format_pending_solution_handoff(workspace=workspace, batch_id=batch_id)
                    return f"{summary}\n{handoff}\nmanager=running"
                shutdown_line = _shutdown_manager_after_terminal_batch(root=root, workspace=workspace)
                return f"{summary}\n{shutdown_line}"
            now = time.monotonic()
            if now - last_activity_at >= _BATCH_MONITOR_HEARTBEAT_SECONDS:
                typer.echo(_format_batch_heartbeat(batch=batch, workspace=workspace, elapsed_seconds=now - started_at))
                last_activity_at = now
            time.sleep(0.75)
    except KeyboardInterrupt:
        return f"batch={batch_id} status=running\n后台批次仍可能继续运行；可用 batch-list 查看状态。"


@app.command()
def onboard():
    """Create the editable workspace scaffold."""
    project_root = _project_root()
    project_rows = _ensure_project_templates(project_root)
    workspace = runtime_workspace_path(project_root)
    rows = bootstrap_workspace(workspace)
    project_created = sum(1 for row in project_rows if row.get("status") == "created")
    project_existing = len(project_rows) - project_created
    created = sum(1 for row in rows if row.get("status") == "created")
    existing = len(rows) - created

    typer.echo(f"Project templates ready at {project_root}")
    typer.echo(f"created={project_created} existing={project_existing}")
    for row in project_rows:
        marker = "+" if row.get("status") == "created" else "="
        typer.echo(f"{marker} {row.get('path')}")
    typer.echo("auth.json contains template fields only; add your own provider API keys.")
    typer.echo(f"Workspace initialized at {workspace}")
    typer.echo(f"created={created} existing={existing}")
    for row in rows:
        marker = "+" if row.get("status") == "created" else "="
        typer.echo(f"{marker} {row.get('path')}")


@app.command()
def run(
    message: str = typer.Option(..., "--message", "-m", help="Message to send"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
):
    """Run one chat turn."""
    typer.echo(_dispatch_message_with_progress(message=message, session=session))


@action_card_app.command("list")
def action_card_list(
    status: str = typer.Option("open", "--status", help="open/done/cancelled/all"),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum cards to show"),
):
    """List action cards for Codex/user follow-up."""
    try:
        rows = ActionCardStore(_workspace_path()).list_cards(status=status, limit=limit)
    except ActionCardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not rows:
        typer.echo("No action cards found.")
        return
    for row in rows:
        typer.echo(
            f"{row.get('card_id')}\t{row.get('status')}\t{row.get('priority') or 'medium'}\t"
            f"{row.get('card_type')}\t{row.get('title')}"
        )


@action_card_app.command("show")
def action_card_show(
    card_id: str = typer.Argument(..., help="Action card ID"),
):
    """Show one action card as Markdown."""
    try:
        typer.echo(ActionCardStore(_workspace_path()).markdown_text(card_id).rstrip())
    except ActionCardError as exc:
        raise typer.BadParameter(str(exc)) from exc


@action_card_app.command("close")
def action_card_close(
    card_id: str = typer.Argument(..., help="Action card ID"),
    result: str = typer.Option("", "--result", help="Review or execution result summary"),
):
    """Mark an action card as done."""
    try:
        card = ActionCardStore(_workspace_path()).close_card(card_id, result_summary=result)
    except ActionCardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"closed={card.get('card_id')} status={card.get('status')} markdown={_workspace_path() / str(card.get('markdown_path') or '')}")


@action_card_app.command("cancel")
def action_card_cancel(
    card_id: str = typer.Argument(..., help="Action card ID"),
    reason: str = typer.Option("", "--reason", help="Cancellation reason"),
):
    """Cancel an action card."""
    try:
        card = ActionCardStore(_workspace_path()).cancel_card(card_id, reason=reason)
    except ActionCardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"cancelled={card.get('card_id')} status={card.get('status')} markdown={_workspace_path() / str(card.get('markdown_path') or '')}")


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
    action = "created" if result.get("created") else "updated"
    typer.echo(f"taskboard {action} id={result.get('taskboard_id')} current={result.get('current_path')}")


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
    state = "updated" if result.get("changed") else "already_done"
    typer.echo(f"taskboard item {state} index={result.get('index')} id={result.get('taskboard_id')}")


@taskboard_app.command("archive")
def taskboard_archive(
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Archive the current development taskboard."""
    try:
        result = TaskboardStore(_workspace_path()).archive()
    except TaskboardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"taskboard archived id={result.get('taskboard_id')} archive={result.get('archive_path')}")


@assistant_app.command("ingest")
def assistant_ingest(
    message: str = typer.Option(..., "--message", "-m", help="Assistant-side user message to classify and store"),
    client: str = typer.Option("codex", "--client", help="External assistant client name"),
    thread: str = typer.Option("default", "--thread", help="External assistant thread/conversation ID"),
    session: str = typer.Option("", "--session", "-s", help="Optional CareerEng session ID"),
    processor: str = typer.Option("local", "--processor", help="Processor adapter backend"),
):
    """Classify and persist an external assistant message for CareerEng."""
    result = ingest_assistant_message(
        workspace=_workspace_path(),
        message=message,
        client=client,
        thread_id=thread,
        session_id=session,
        processor_backend=processor,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@assistant_app.command("context")
def assistant_context(
    recent_limit: int = typer.Option(8, "--recent-limit", help="Recent rows/files to include per context section"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Build the assistant-readable CareerEng context pack."""
    result = build_assistant_context_pack(
        project_root=_project_root(),
        workspace=_workspace_path(),
        recent_limit=recent_limit,
    )
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"assistant_context={result.get('path')}")


@assistant_app.command("state")
def assistant_state(
    client: str = typer.Option("codex", "--client", help="External assistant client name"),
    thread: str = typer.Option("", "--thread", help="Optional external assistant thread/conversation ID"),
):
    """Show assistant bridge thread scope state."""
    store = AssistantThreadStateStore(_workspace_path())
    if thread:
        payload = store.get(client=client, thread_id=thread)
    else:
        payload = store.load()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@assistant_app.command("end")
def assistant_end(
    client: str = typer.Option("codex", "--client", help="External assistant client name"),
    thread: str = typer.Option("default", "--thread", help="External assistant thread/conversation ID"),
):
    """Close an active assistant bridge career scope."""
    payload = AssistantThreadStateStore(_workspace_path()).close_scope(client=client, thread_id=thread)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@assistant_app.command("import-candidates")
def assistant_import_candidates(
    input_file: Path = typer.Argument(..., help="JSON or JSONL file of assistant-curated memory candidates"),
    source_limit: int = typer.Option(0, "--source-limit", help="Number of recent assistant messages summarized; 0 means unspecified"),
    source_thread: str = typer.Option("codex-current", "--source-thread", help="External assistant thread/conversation ID"),
    source_client: str = typer.Option("codex", "--source-client", help="External assistant client name"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Import assistant-curated recent conversation candidates into career memory."""
    try:
        result = import_memory_candidates(
            workspace=_workspace_path(),
            input_path=input_file,
            source_limit=source_limit,
            source_thread=source_thread,
            source_client=source_client,
        )
    except CareerMemoryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    scope = f"recent_{source_limit}_messages" if source_limit > 0 else "recent_messages"
    typer.echo(
        f"assistant memory imported created={result.get('created')} "
        f"lessons={result.get('created_lessons', 0)} "
        f"evidence={result.get('created_evolution_evidence', 0)} "
        f"skipped_existing={result.get('skipped_existing')} read={result.get('read')} "
        f"thread={result.get('source_thread') or '-'} scope={scope}"
    )
    typer.echo(f"memory_units={result.get('memory_units_path')}")


@assistant_app.command("import-recent")
def assistant_import_recent(
    input_file: Path = typer.Argument(..., help="JSON or JSONL file of Codex-curated recent conversation candidates"),
    limit: int = typer.Option(..., "--limit", help="Number of recent assistant messages summarized"),
    source_thread: str = typer.Option("codex-current", "--source-thread", help="External assistant thread/conversation ID"),
    source_client: str = typer.Option("codex", "--source-client", help="External assistant client name"),
    recent_limit: int = typer.Option(8, "--context-recent-limit", help="Recent rows/files to include when refreshing assistant context"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Import recent conversation candidates, record intake state, and refresh assistant context."""
    try:
        result = import_memory_candidates(
            workspace=_workspace_path(),
            input_path=input_file,
            source_limit=limit,
            source_thread=source_thread,
            source_client=source_client,
        )
    except CareerMemoryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    context_path = _workspace_path() / "assistant_bridge" / "context" / "latest.md"
    state = save_recent_intake_state(
        workspace=_workspace_path(),
        import_result=result,
        source_file=input_file,
        source_limit=limit,
        source_thread=source_thread,
        source_client=source_client,
        context_path=context_path,
    )
    context_result = build_assistant_context_pack(
        project_root=_project_root(),
        workspace=_workspace_path(),
        recent_limit=recent_limit,
    )
    payload = {
        "import": result,
        "intake_state": state,
        "assistant_context": context_result,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(
        f"assistant recent imported created={result.get('created')} "
        f"lessons={result.get('created_lessons', 0)} "
        f"evidence={result.get('created_evolution_evidence', 0)} "
        f"skipped_existing={result.get('skipped_existing')} read={result.get('read')} "
        f"limit={limit} thread={source_thread or '-'}"
    )
    typer.echo(f"intake_state={_workspace_path() / 'assistant_bridge' / 'intake_state.json'}")
    typer.echo(f"assistant_context={context_result.get('path')}")


@career_memory_app.command("promote")
def career_memory_promote(
    limit: int = typer.Option(0, "--limit", help="Maximum new memory units to create; 0 means no limit"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Promote assistant bridge signals into unified career memory units."""
    result = promote_assistant_signals(workspace=_workspace_path(), limit=limit if limit > 0 else None)
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(
        f"career-memory promoted created={result.get('created')} "
        f"skipped_existing={result.get('skipped_existing')} scanned={result.get('scanned')}"
    )
    typer.echo(f"memory_units={result.get('memory_units_path')}")


@career_memory_app.command("import-candidates")
def career_memory_import_candidates(
    input_file: Path = typer.Argument(..., help="JSON or JSONL file of Codex-curated memory candidates"),
    source_limit: int = typer.Option(0, "--source-limit", help="Number of recent assistant messages summarized; 0 means unspecified"),
    source_thread: str = typer.Option("", "--source-thread", help="Optional external assistant thread/conversation ID"),
    source_client: str = typer.Option("", "--source-client", help="Optional external assistant client name"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Import Codex-curated career memory candidates after schema validation."""
    try:
        result = import_memory_candidates(
            workspace=_workspace_path(),
            input_path=input_file,
            source_limit=source_limit,
            source_thread=source_thread,
            source_client=source_client,
        )
    except CareerMemoryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(
        f"career-memory imported created={result.get('created')} "
        f"lessons={result.get('created_lessons', 0)} "
        f"evidence={result.get('created_evolution_evidence', 0)} "
        f"skipped_existing={result.get('skipped_existing')} read={result.get('read')}"
    )
    typer.echo(f"memory_units={result.get('memory_units_path')}")


@career_memory_app.command("list")
def career_memory_list(
    category: str = typer.Option("", "--category", help="Filter by memory category"),
    status: str = typer.Option("", "--status", help="Filter by memory status"),
    limit: int = typer.Option(20, "--limit", help="Maximum rows to print"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """List unified career memory units."""
    rows = list_memory_units(workspace=_workspace_path(), category=category, status=status, limit=limit)
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not rows:
        typer.echo("No career memory units found.")
        return
    for row in rows:
        typer.echo(
            f"- {row.get('memory_id')} [{row.get('category')}/{row.get('status')}] "
            f"{row.get('summary') or ''}"
        )


@career_memory_app.command("show")
def career_memory_show(
    memory_id: str = typer.Argument(..., help="Memory unit ID"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Show one unified career memory unit."""
    try:
        row = show_memory_unit(workspace=_workspace_path(), memory_id=memory_id)
    except CareerMemoryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"{row.get('memory_id')} [{row.get('category')}/{row.get('status')}]")
    typer.echo(str(row.get("summary") or ""))
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    if tags:
        typer.echo("tags: " + ", ".join(str(tag) for tag in tags))
    if row.get("source_text"):
        typer.echo("source:")
        typer.echo(str(row.get("source_text") or ""))


def _interview_store() -> InterviewStore:
    return InterviewStore(_workspace_path())


def _print_audio_devices() -> None:
    try:
        rows = list_audio_devices()
    except AudioCaptureDependencyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not rows:
        typer.echo("No audio devices found.")
        return
    for row in rows:
        marker = "input" if row.get("is_input") else "output"
        typer.echo(
            f"{row.get('index')}\t{marker}\t{row.get('name')}\t"
            f"in={row.get('input_channels')} out={row.get('output_channels')} "
            f"rate={row.get('default_samplerate')} host={row.get('hostapi')}"
        )


@capture_audio_app.command("devices")
def capture_audio_devices():
    """List local audio devices for capture."""
    _print_audio_devices()


@interview_app.command("create")
def interview_create(
    company: str = typer.Option("unknown", "--company", help="Company name"),
    title: str = typer.Option("unknown", "--title", help="Job title"),
    site: str = typer.Option("", "--site", help="Site key"),
    url: str = typer.Option("", "--url", help="Job or application URL"),
    site_job_id: str = typer.Option("", "--site-job-id", help="Site-native job ID"),
    canonical_job_id: str = typer.Option("", "--canonical-job-id", help="CareerEng canonical job ID"),
    application_status: str = typer.Option("", "--application-status", help="Current application status"),
    application_stage: str = typer.Option("", "--application-stage", help="Current application stage"),
    source_history_ref: str = typer.Option("", "--source-history-ref", help="History job reference"),
    created_reason: str = typer.Option("manual_prep", "--created-reason", help="manual_prep/status_in_process/teams_meeting/codex_prep"),
    source_ref: str = typer.Option("", "--source-ref", help="Comma-separated source refs"),
):
    """Create an interview session bound to a job/application."""
    try:
        row = _interview_store().create_session(
            company=company,
            title=title,
            site_key=site,
            url=url,
            site_job_id=site_job_id,
            canonical_job_id=canonical_job_id,
            application_status=application_status,
            application_stage=application_stage,
            source_history_ref=source_history_ref,
            created_reason=created_reason,
            source_refs=_csv_list(source_ref),
        )
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"interview_session={row.get('session_id')} status={row.get('status')}")
    typer.echo(f"company={row.get('company')} title={row.get('title') or '-'}")


@interview_app.command("update")
def interview_update(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    company: str | None = typer.Option(None, "--company", help="Company name"),
    title: str | None = typer.Option(None, "--title", help="Job title"),
    site: str | None = typer.Option(None, "--site", help="Site key"),
    url: str | None = typer.Option(None, "--url", help="Job or application URL"),
    site_job_id: str | None = typer.Option(None, "--site-job-id", help="Site-native job ID"),
    canonical_job_id: str | None = typer.Option(None, "--canonical-job-id", help="CareerEng canonical job ID"),
    application_status: str | None = typer.Option(None, "--application-status", help="Current application status"),
    application_stage: str | None = typer.Option(None, "--application-stage", help="Current application stage"),
    source_history_ref: str | None = typer.Option(None, "--source-history-ref", help="History job reference"),
    source_ref: str = typer.Option("", "--source-ref", help="Comma-separated source refs to append"),
):
    """Update or enrich an interview session after more context is known."""
    try:
        row = _interview_store().update_session(
            session_id,
            company=company,
            title=title,
            site_key=site,
            url=url,
            site_job_id=site_job_id,
            canonical_job_id=canonical_job_id,
            application_status=application_status,
            application_stage=application_stage,
            source_history_ref=source_history_ref,
            source_refs=_csv_list(source_ref) if source_ref else None,
        )
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"interview_session={row.get('session_id')} updated")
    typer.echo(f"company={row.get('company')} title={row.get('title') or '-'}")


@interview_app.command("candidates")
def interview_candidates(
    company: str = typer.Option("", "--company", help="Company name or site alias"),
    title: str = typer.Option("", "--title", help="Job title or role keywords"),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum candidates to show"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
):
    """Find local job/application candidates before creating an interview session."""
    rows = save_interview_candidates(workspace=_workspace_path(), company=company, title=title, limit=limit)
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not rows:
        typer.echo("No interview candidates found.")
        typer.echo("Use `careereng interview create --company ... --title ...` for a manual prep session.")
        return
    for row in rows:
        typer.echo(
            f"{row.get('candidate_id')}\tscore={row.get('match_score')}\t"
            f"{row.get('company') or row.get('site_key')}\t{row.get('title') or '-'}"
        )
        typer.echo(
            f"  site={row.get('site_key') or '-'} site_job_id={row.get('site_job_id') or '-'} "
            f"stage={row.get('application_stage') or '-'} status={row.get('application_status') or '-'}"
        )
        if row.get("url"):
            typer.echo(f"  url={row.get('url')}")
        if row.get("match_reason"):
            typer.echo(f"  reason={row.get('match_reason')}")


@interview_app.command("create-from-candidate")
def interview_create_from_candidate(
    candidate_id: str = typer.Option(..., "--candidate-id", help="Candidate ID from `interview candidates`"),
):
    """Create or reuse an interview session after the user confirms a local candidate."""
    try:
        row, created = _interview_store().create_session_from_candidate(candidate_id)
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    state = "created" if created else "existing"
    typer.echo(f"interview_session={row.get('session_id')} {state}")
    typer.echo(f"company={row.get('company')} title={row.get('title') or '-'}")
    typer.echo(f"site={row.get('site_key') or '-'} site_job_id={row.get('site_job_id') or '-'}")


@interview_app.command("add-prep-event")
def interview_add_prep_event(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    summary: str = typer.Option(..., "--summary", help="Structured preparation summary"),
    event_type: str = typer.Option("note", "--type", help="predicted_question/answer_strategy/skill_gap/project_story/learning_plan/resume_signal/note"),
    details: str = typer.Option("", "--details", help="Optional details"),
    tags: str = typer.Option("", "--tags", help="Comma-separated topic tags"),
    source_ref: str = typer.Option("", "--source-ref", help="Comma-separated assistant bridge or transcript refs"),
    memory_ref: str = typer.Option("", "--memory-ref", help="Comma-separated career memory refs"),
):
    """Attach structured interview-prep information to a session."""
    try:
        row = _interview_store().add_prep_event(
            session_id,
            event_type=event_type,
            summary=summary,
            details=details,
            topic_tags=_csv_list(tags),
            source_refs=_csv_list(source_ref),
            memory_refs=_csv_list(memory_ref),
        )
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"prep_event={row.get('prep_event_id')} session={row.get('session_id')}")


@interview_app.command("add-question")
def interview_add_question(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    question: str = typer.Option(..., "--question", help="Predicted interview question"),
    reason: str = typer.Option("", "--reason", help="Why this question is expected"),
    topics: str = typer.Option("", "--topics", help="Comma-separated expected topics"),
    answer_outline: str = typer.Option("", "--answer-outline", help="Suggested answer outline"),
    source_ref: str = typer.Option("", "--source-ref", help="Comma-separated source refs"),
):
    """Add a predicted interview question for later hit/miss comparison."""
    try:
        row = _interview_store().add_predicted_question(
            session_id,
            question=question,
            reason=reason,
            expected_topics=_csv_list(topics),
            suggested_answer_outline=answer_outline,
            source_refs=_csv_list(source_ref),
        )
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"question={row.get('question_id')} session={row.get('session_id')}")


@interview_app.command("add-turn")
def interview_add_turn(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    text: str = typer.Option(..., "--text", help="Transcript turn text"),
    speaker: str = typer.Option("unknown", "--speaker", help="interviewer/candidate/assistant/unknown"),
    text_type: str = typer.Option("note", "--type", help="question/answer/followup/note"),
    source: str = typer.Option("manual", "--source", help="manual/codex/teams/transcript"),
    tags: str = typer.Option("", "--tags", help="Comma-separated topic tags"),
    linked_question_id: str = typer.Option("", "--linked-question-id", help="Predicted question ID"),
):
    """Add one real interview transcript turn."""
    try:
        row = _interview_store().add_turn(
            session_id,
            raw_text=text,
            speaker=speaker,
            text_type=text_type,
            source=source,
            topic_tags=_csv_list(tags),
            linked_question_id=linked_question_id,
        )
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"turn={row.get('turn_id')} session={row.get('session_id')}")


@interview_app.command("add-suggestion")
def interview_add_suggestion(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    suggested_answer: str = typer.Option(..., "--suggested-answer", help="LLM suggested answer or hint"),
    linked_turn_id: str = typer.Option("", "--linked-turn-id", help="Question turn this suggestion responds to"),
    strategy_notes: str = typer.Option("", "--strategy-notes", help="Optional strategy notes"),
    adoption_status: str = typer.Option("unknown", "--adoption-status", help="adopted/partially_adopted/ignored/unknown"),
    actual_answer_turn_id: str = typer.Option("", "--actual-answer-turn-id", help="Candidate answer turn ID"),
    difference_notes: str = typer.Option("", "--difference-notes", help="Difference between suggestion and actual answer"),
    source_ref: str = typer.Option("", "--source-ref", help="Comma-separated source refs"),
):
    """Record an LLM suggestion and whether the candidate used it."""
    try:
        row = _interview_store().add_suggestion(
            session_id,
            suggested_answer=suggested_answer,
            linked_turn_id=linked_turn_id,
            strategy_notes=strategy_notes,
            adoption_status=adoption_status,
            actual_answer_turn_id=actual_answer_turn_id,
            difference_notes=difference_notes,
            source_refs=_csv_list(source_ref),
        )
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"suggestion={row.get('suggestion_id')} session={row.get('session_id')}")


@interview_app.command("add-evidence")
def interview_add_evidence(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    evidence_type: str = typer.Option(..., "--type", help="predicted_question_hit/unexpected_question/skill_gap/resume_signal/company_signal/answer_quality_signal/preparation_gap"),
    summary: str = typer.Option(..., "--summary", help="Evidence summary"),
    details: str = typer.Option("", "--details", help="Optional details"),
    source_ref: str = typer.Option("", "--source-ref", help="Comma-separated source refs"),
    confidence: float = typer.Option(0.0, "--confidence", help="Confidence score"),
    severity: str = typer.Option("medium", "--severity", help="low/medium/high"),
):
    """Add interview evidence and sync it into evolution evidence."""
    try:
        row = _interview_store().add_evidence(
            session_id,
            evidence_type=evidence_type,
            summary=summary,
            details=details,
            source_refs=_csv_list(source_ref),
            confidence=confidence,
            severity=severity,
        )
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"evidence={row.get('evidence_id')} session={row.get('session_id')}")


@interview_app.command("show")
def interview_show(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    recent_limit: int = typer.Option(5, "--recent-limit", min=1, help="Recent records per section"),
):
    """Show one interview session summary."""
    try:
        summary = build_interview_summary(_interview_store(), session_id, recent_limit=recent_limit)
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(render_interview_summary(summary).rstrip())


@interview_app.command("audio-devices")
def interview_audio_devices():
    """List audio devices for interview capture."""
    _print_audio_devices()


@interview_app.command("capture-audio")
def interview_capture_audio(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    device: str = typer.Option("", "--device", help="Input device index or name"),
    sample_rate: int = typer.Option(16000, "--sample-rate", help="Recording sample rate"),
    channels: int = typer.Option(1, "--channels", min=1, help="Input channel count"),
):
    """Capture audio chunks for an interview session using q/a/n/s key markers."""
    store = _interview_store()
    try:
        store.get_session(session_id)
        output_dir = _workspace_path() / "interviews" / session_id / "audio" / "chunks"
        chunks = capture_audio_chunks(
            output_dir=output_dir,
            device=device or None,
            sample_rate=sample_rate,
            channels=channels,
        )
        saved = [store.add_audio_chunk(session_id, chunk) for chunk in chunks]
    except (InterviewStoreError, AudioCaptureDependencyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"audio_chunks={len(saved)} session={session_id}")
    typer.echo(f"audio_dir={output_dir}")


@profile_app.command("generate")
def profile_generate(
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    message: str = typer.Option(PROFILE_GENERATE_MESSAGE, "--message", "-m", help="Profile generation prompt"),
):
    """Generate or update persona.md through the normal agent flow."""
    typer.echo(_dispatch_message_with_progress(message=message, session=session))


@jobs_app.command("apply")
def jobs_apply(
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    message: str = typer.Option(JOBS_APPLY_MESSAGE, "--message", "-m", help="Registered-sites retrieval/apply prompt"),
):
    """Retrieve and apply jobs for active registered sites."""
    typer.echo(
        _dispatch_jobs_batch_with_monitor(
            operation="job_search",
            apply_requested=True,
            message=message,
            session=session,
        )
    )


@jobs_app.command("review-status")
def jobs_review_status(
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    message: str = typer.Option(JOBS_REVIEW_STATUS_MESSAGE, "--message", "-m", help="Application status review prompt"),
):
    """Review submitted application statuses for active registered sites."""
    typer.echo(
        _dispatch_jobs_batch_with_monitor(
            operation="application_status_review",
            apply_requested=False,
            message=message,
            session=session,
        )
    )


def _emit_agent_bridge_browser_tools(*, site: str, json_output: bool, legacy: bool = False) -> None:
    list_tools = list_browser_handoff_tools if legacy else list_agent_bridge_browser_tools
    response = list_tools(
        project_root=_project_root(),
        workspace=_workspace_path(),
        site_key=site,
    )
    if json_output:
        typer.echo(json.dumps(response, ensure_ascii=False, indent=2))
        return
    tools = response.get("tools") if isinstance(response.get("tools"), list) else []
    typer.echo(f"site={response.get('site_key') or site} tools={len(tools)}")
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        description = str(tool.get("description") or "").strip()
        line = f"- {name}"
        if description:
            line += f": {description[:160]}"
        typer.echo(line)


def _emit_agent_bridge_browser_call(
    *,
    site: str,
    tool: str,
    args: str,
    phase: str,
    turn: str,
    json_output: bool,
    legacy: bool = False,
) -> None:
    try:
        parsed_args = json.loads(args or "{}")
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--args must be a JSON object: {exc}") from exc
    if not isinstance(parsed_args, dict):
        raise typer.BadParameter("--args must be a JSON object")
    call_tool = call_browser_handoff_tool if legacy else call_agent_bridge_browser_tool
    response = call_tool(
        project_root=_project_root(),
        workspace=_workspace_path(),
        site_key=site,
        tool_name=tool,
        arguments=parsed_args,
        turn_id=turn,
        phase=phase,
    )
    if json_output:
        typer.echo(json.dumps(response, ensure_ascii=False, indent=2))
        return
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    status = "ok" if bool(result.get("ok")) else "error"
    typer.echo(
        f"site={response.get('site_key') or site} tool={tool} status={status} "
        f"url={result.get('current_url') or ''} trace={result.get('trace_ref') or ''}"
    )
    summary = str(result.get("summary") or "").strip()
    if summary:
        typer.echo(summary)


def _emit_agent_bridge_state_tools(*, site: str, phase: str, json_output: bool) -> None:
    response = list_agent_bridge_state_tools(
        project_root=_project_root(),
        workspace=_workspace_path(),
        site_key=site,
        phase=phase,
    )
    if json_output:
        typer.echo(json.dumps(response, ensure_ascii=False, indent=2))
        return
    tools = response.get("tools") if isinstance(response.get("tools"), list) else []
    phase_text = str(response.get("phase") or phase or "").strip()
    suffix = f" phase={phase_text}" if phase_text else ""
    typer.echo(f"site={response.get('site_key') or site}{suffix} state_tools={len(tools)}")
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        description = str(tool.get("description") or "").strip()
        line = f"- {name}"
        if description:
            line += f": {description[:160]}"
        typer.echo(line)


def _emit_agent_bridge_state_call(
    *,
    site: str,
    tool: str,
    args: str,
    phase: str,
    turn: str,
    json_output: bool,
) -> None:
    try:
        parsed_args = json.loads(args or "{}")
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--args must be a JSON object: {exc}") from exc
    if not isinstance(parsed_args, dict):
        raise typer.BadParameter("--args must be a JSON object")
    response = call_agent_bridge_state_tool(
        project_root=_project_root(),
        workspace=_workspace_path(),
        site_key=site,
        tool_name=tool,
        arguments=parsed_args,
        turn_id=turn,
        phase=phase,
    )
    if json_output:
        typer.echo(json.dumps(response, ensure_ascii=False, indent=2))
        return
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    status = "ok" if bool(result.get("ok")) else "error"
    typer.echo(
        f"site={response.get('site_key') or site} state_tool={tool} status={status} "
        f"phase={result.get('phase') or phase or ''} trace={result.get('trace_ref') or ''}"
    )
    summary = str(result.get("summary") or "").strip()
    if summary:
        typer.echo(summary)


@agent_bridge_app.command("browser-tools")
def agent_bridge_browser_tools(
    site: str = typer.Option(..., "--site", help="Site key with an active agent bridge browser runtime"),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response"),
):
    """List MCP browser tools exposed by the active CareerEng agent bridge runtime."""
    _emit_agent_bridge_browser_tools(site=site, json_output=json_output)


@agent_bridge_app.command("browser-call")
def agent_bridge_browser_call(
    site: str = typer.Option(..., "--site", help="Site key with an active agent bridge browser runtime"),
    tool: str = typer.Option(..., "--tool", help="MCP browser tool name, e.g. browser_snapshot"),
    args: str = typer.Option("{}", "--args", help="JSON object arguments for the tool"),
    phase: str = typer.Option(AGENT_BRIDGE_STATUS, "--phase", help="Trace phase label"),
    turn: str = typer.Option("", "--turn", help="Optional turn ID for trace linkage"),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response"),
):
    """Call one MCP browser tool through the active CareerEng agent bridge runtime."""
    _emit_agent_bridge_browser_call(
        site=site,
        tool=tool,
        args=args,
        phase=phase,
        turn=turn,
        json_output=json_output,
    )


@agent_bridge_app.command("state-tools")
def agent_bridge_state_tools(
    site: str = typer.Option(..., "--site", help="Site key with an active agent bridge phase session"),
    phase: str = typer.Option("", "--phase", help="Optional phase override, e.g. apply"),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response"),
):
    """List CareerEng state tools exposed by the active phase session."""
    _emit_agent_bridge_state_tools(site=site, phase=phase, json_output=json_output)


@agent_bridge_app.command("state-call")
def agent_bridge_state_call(
    site: str = typer.Option(..., "--site", help="Site key with an active agent bridge phase session"),
    tool: str = typer.Option(..., "--tool", help="CareerEng state tool name, e.g. update_jobs"),
    args: str = typer.Option("{}", "--args", help="JSON object arguments for the state tool"),
    phase: str = typer.Option("", "--phase", help="Optional phase override, e.g. apply"),
    turn: str = typer.Option("", "--turn", help="Optional turn ID for trace linkage"),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response"),
):
    """Call one CareerEng state tool through the active phase session."""
    _emit_agent_bridge_state_call(
        site=site,
        tool=tool,
        args=args,
        phase=phase,
        turn=turn,
        json_output=json_output,
    )


@agent_bridge_app.command("phase-result")
def agent_bridge_phase_result(
    site: str = typer.Option(..., "--site", help="Site key with an active agent bridge phase session"),
    status: str = typer.Option(..., "--status", help="Phase result status: done or blocked"),
    summary: str = typer.Option(..., "--summary", help="Short phase result summary"),
    phase: str = typer.Option("", "--phase", help="Optional phase override, e.g. apply"),
    turn: str = typer.Option("", "--turn", help="Optional turn ID for trace linkage"),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response"),
):
    """Record a phase_result through the shared CareerEng state tool path."""
    _emit_agent_bridge_state_call(
        site=site,
        tool="phase_result",
        args=json.dumps({"status": status, "summary": summary}, ensure_ascii=False),
        phase=phase,
        turn=turn,
        json_output=json_output,
    )


@browser_handoff_app.command("tools")
def browser_handoff_tools(
    site: str = typer.Option(..., "--site", help="Site key with an active agent bridge browser runtime"),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response"),
):
    """Legacy alias for `agent-bridge browser-tools`."""
    _emit_agent_bridge_browser_tools(site=site, json_output=json_output, legacy=True)


@browser_handoff_app.command("call")
def browser_handoff_call(
    site: str = typer.Option(..., "--site", help="Site key with an active agent bridge browser runtime"),
    tool: str = typer.Option(..., "--tool", help="MCP browser tool name, e.g. browser_snapshot"),
    args: str = typer.Option("{}", "--args", help="JSON object arguments for the tool"),
    phase: str = typer.Option(AGENT_BRIDGE_STATUS, "--phase", help="Trace phase label"),
    turn: str = typer.Option("", "--turn", help="Optional turn ID for trace linkage"),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON response"),
):
    """Legacy alias for `agent-bridge browser-call`."""
    _emit_agent_bridge_browser_call(
        site=site,
        tool=tool,
        args=args,
        phase=phase,
        turn=turn,
        json_output=json_output,
        legacy=True,
    )


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
        "Metrics Summary",
        f"- source: {summary.get('source_path')}",
        f"- batch: {filters.get('batch_id') or 'all'}",
        f"- site: {filters.get('site_key') or 'all'}",
        f"- phase: {filters.get('phase') or 'all'}",
        f"- calls: {_format_int(totals.get('calls'))}",
        f"- ok calls: {_format_int(totals.get('ok_calls'))}",
        f"- error calls: {_format_int(totals.get('error_calls'))}",
        f"- elapsed: {_format_duration(int(totals.get('elapsed_ms') or 0))}",
        f"- input tokens: {_format_int(totals.get('input_tokens'))}",
        f"- output tokens: {_format_int(totals.get('output_tokens'))}",
        f"- total tokens: {_format_int(totals.get('total_tokens'))}",
        f"- unknown token calls: {_format_int(totals.get('unknown_token_calls'))}",
        "",
    ]
    groups = summary.get("groups") if isinstance(summary.get("groups"), dict) else {}
    for title, key in (
        ("By Site", "site_key"),
        ("By Phase", "phase"),
        ("By Model", "model"),
        ("By API Type", "api_type"),
        ("By Status", "status"),
    ):
        lines.extend(_metrics_group_lines(title, groups.get(key) if isinstance(groups.get(key), list) else []))
        lines.append("")
    errors = summary.get("error_rows") if isinstance(summary.get("error_rows"), list) else []
    lines.append("Errors")
    if not errors:
        lines.append("- none")
    else:
        for row in errors[:50]:
            lines.append(
                f"- {row.get('ts')} batch={row.get('batch_id') or '-'} "
                f"site={row.get('site_key') or '-'} phase={row.get('phase') or '-'} "
                f"error={row.get('error_type') or row.get('status') or 'error'}"
            )
        if len(errors) > 50:
            lines.append(f"- ... {len(errors) - 50} more")
    if save:
        path = save_metrics_summary(summary, workspace=workspace)
        lines.extend(["", f"saved: {path}"])
    typer.echo("\n".join(lines).rstrip())


@evolution_app.command("review")
def evolution_review(
    max_evidence: int = typer.Option(200, "--max-evidence", min=1, help="Maximum recent evidence rows to include"),
):
    """Build an evidence-backed evolution review and context pack."""
    workspace = _workspace_path()
    review = build_evolution_review(workspace=workspace, project_root=_project_root(), max_evidence=max_evidence)
    paths = save_evolution_review(review, workspace=workspace)
    lines = [
        "Evolution Review",
        f"- evidence: {_format_int(review.get('evidence_count'))}",
        f"- open candidates: {_format_int(review.get('candidate_count'))}",
        f"- memory units: {_format_int(review.get('memory_count'))}",
        f"- review: {paths['review_markdown']}",
        f"- review_json: {paths['review_json']}",
        f"- context: {paths['context_markdown']}",
        f"- candidates: {paths['open_candidates_store']}",
    ]
    typer.echo("\n".join(lines))


@evolution_app.command("candidates")
def evolution_candidates():
    """List available evolution candidate specs."""
    specs = load_candidate_specs(_project_root())
    if not specs:
        typer.echo("No evolution candidate specs found.")
        return
    for spec in specs:
        typer.echo(f"{spec.id}\t{spec.risk_level}\t{spec.target_type}\t{spec.target_ref}")


@evolution_app.command("lessons")
def evolution_lessons(
    status: str = typer.Option("accepted", "--status", help="Lesson status to show; empty means all"),
    site: str = typer.Option("", "--site", help="Optional site key filter"),
    phase: str = typer.Option("", "--phase", help="Optional browser phase filter"),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum lessons to show"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON rows instead of Markdown"),
):
    """List durable browser-control lessons used by evolution."""
    store = BrowserControlLessonStore(_workspace_path())
    rows = store.list(status=status, site_key=site, phase=phase, limit=limit)
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(render_lessons_markdown(rows, limit=limit).rstrip())


@evolution_app.command("candidate-show")
def evolution_candidate_show(
    candidate_id: str = typer.Argument(..., help="Evolution candidate ID"),
    json_output: bool = typer.Option(False, "--json", help="Print structured JSON instead of Markdown body"),
):
    """Show one evolution candidate spec."""
    try:
        spec = get_candidate_spec(_project_root(), candidate_id)
    except CandidateSpecError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return
    lines = [
        f"# {spec.name}",
        "",
        f"- id: `{spec.id}`",
        f"- target_type: `{spec.target_type}`",
        f"- target_ref: `{spec.target_ref}`",
        f"- risk_level: `{spec.risk_level}`",
        f"- apply_policy: `{spec.apply_policy}`",
        f"- path: `{spec.path}`",
        "",
        spec.body,
    ]
    typer.echo("\n".join(lines).rstrip())


@evolution_app.command("run")
def evolution_run(
    candidate: str = typer.Option(..., "--candidate", "-c", help="Evolution candidate ID"),
):
    """Create an archived evolution run and evidence pack for a candidate."""
    try:
        result = create_evolution_run(project_root=_project_root(), workspace=_workspace_path(), candidate_id=candidate)
    except CandidateSpecError as exc:
        raise typer.BadParameter(str(exc)) from exc
    lines = [
        f"run={result['run_id']} status={result['status']}",
        f"candidate={result['candidate_id']}",
        f"run_dir={result['run_dir']}",
        f"run_json={result['run_json']}",
        f"evidence_pack={result['evidence_pack']}",
        f"summary={result['summary']}",
    ]
    typer.echo("\n".join(lines))


@evolution_app.command("solution")
def evolution_solution(
    card: str = typer.Option("", "--card", help="Action card ID to convert into a Codex solution request"),
    run: str = typer.Option("", "--run", help="Existing evolution run ID to refresh solution request"),
    candidate: str = typer.Option("", "--candidate", "-c", help="Candidate spec ID when the action card lacks one"),
):
    """Create a Codex-readable solution request and proposal output path."""
    normalized_card = str(card or "").strip()
    normalized_run = str(run or "").strip()
    if bool(normalized_card) == bool(normalized_run):
        raise typer.BadParameter("Pass exactly one of --card or --run.")
    try:
        if normalized_card:
            result = create_solution_request_for_action_card(
                project_root=_project_root(),
                workspace=_workspace_path(),
                card_id=normalized_card,
                candidate_id=candidate,
            )
        else:
            result = create_solution_request_for_run(
                project_root=_project_root(),
                workspace=_workspace_path(),
                run_id=normalized_run,
            )
    except EvolutionSolutionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    lines = [
        f"run={result['run_id']} status={result['status']}",
        f"candidate={result['candidate_id']}",
        f"action_card={result.get('action_card_id') or ''}",
        f"solution_request={result['solution_request']}",
        f"proposal_output={result['proposal_output_path']}",
        f"apply_command=python -m careereng evolution apply --run {result['run_id']}",
    ]
    typer.echo("\n".join(lines))


@evolution_app.command("apply")
def evolution_apply(
    run: str = typer.Option(..., "--run", help="Evolution run ID"),
):
    """Apply a rollbackable proposal from an evolution run archive."""
    try:
        result = apply_evolution_run(workspace=_workspace_path(), project_root=_project_root(), run_id=run)
    except (EvolutionApplyError, EvolutionProposalError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    lines = [
        f"run={result['run_id']} status={result['status']}",
        f"applied_count={result['applied_count']}",
        f"applied_files={result['applied_files']}",
        f"applied_patch={result['applied_patch']}",
        f"summary={result['summary']}",
    ]
    typer.echo("\n".join(lines))


@evolution_app.command("pending-solution")
def evolution_pending_solution(
    site: str = typer.Option("", "--site", help="Optional site key filter"),
    batch: str = typer.Option("", "--batch", help="Optional batch ID filter"),
    limit: int = typer.Option(5, "--limit", min=1, help="Maximum pending solution requests to show"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON rows instead of a concise handoff view"),
):
    """Show pending evolution solution requests for Codex or another assistant."""
    rows = list_pending_solution_requests(
        workspace=_workspace_path(),
        site_key=site,
        batch_id=batch,
        limit=limit,
    )
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not rows:
        typer.echo("No pending evolution solution requests.")
        return
    lines: list[str] = []
    for row in rows:
        if lines:
            lines.append("")
        lines.extend(
            [
                f"run={row['run_id']} status={row['status']} next_action={row['next_action']}",
                f"candidate={row['candidate_id']} site={row['site_key']} phase={row['phase']} batch={row['batch_id']}",
                f"failure_pattern={row['failure_pattern']}",
                f"solution_request={row['solution_request']}",
                f"proposal_output={row['proposal_output_path']}",
                f"apply_command={row['apply_command']}",
            ]
        )
    typer.echo("\n".join(lines))


@evolution_app.command("continue-batch")
def evolution_continue_batch(
    batch: str = typer.Option(..., "--batch", help="Batch ID to continue after an evolution solution"),
    site: str = typer.Option("", "--site", help="Optional site key filter for pending solution lookup"),
):
    """Apply a written proposal if present, then continue the outer evolution batch loop."""
    rows = list_pending_solution_requests(
        workspace=_workspace_path(),
        site_key=site,
        batch_id=batch,
        limit=1,
    )
    lines: list[str] = []
    if rows:
        row = rows[0]
        if not bool(row.get("proposal_exists")):
            lines.extend(
                [
                    f"batch={batch} status=waiting_solution next_action=write_proposal",
                    f"run={row['run_id']}",
                    f"solution_request={row['solution_request']}",
                    f"proposal_output={row['proposal_output_path']}",
                    f"apply_command={row['apply_command']}",
                    f"continue_command=python -m careereng evolution continue-batch --batch {batch}",
                ]
            )
            typer.echo("\n".join(lines))
            return
        try:
            applied = apply_evolution_run(
                workspace=_workspace_path(),
                project_root=_project_root(),
                run_id=str(row.get("run_id") or ""),
            )
        except (EvolutionApplyError, EvolutionProposalError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        lines.extend(
            [
                f"applied_run={applied['run_id']} status={applied['status']}",
                f"applied_count={applied['applied_count']}",
            ]
        )

    loop, _ = _build_loop()
    try:
        try:
            reply = BatchEvolutionOrchestrator(loop.job_flow).continue_after_solution(batch)
        finally:
            _close_loop_if_possible(loop)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if lines:
        lines.append("")
    lines.append(reply)
    typer.echo("\n".join(lines))


@evolution_app.command("evaluate")
def evolution_evaluate(
    run: str = typer.Option(..., "--run", help="Evolution run ID"),
    recent_limit: int = typer.Option(10, "--recent-limit", min=1, help="Recent rows to compare before/after apply"),
):
    """Evaluate an applied run, or generate a review pack for review-only runs."""
    try:
        result = evaluate_evolution_run(
            workspace=_workspace_path(),
            project_root=_project_root(),
            run_id=run,
            recent_limit=recent_limit,
        )
    except EvolutionEvaluationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    lines = [
        f"run={result['run_id']} status={result['status']} selection={result['selection']}",
        f"evaluation={result['evaluation']}",
        f"evaluation_markdown={result['evaluation_markdown']}",
        f"selection_json={result['selection_json']}",
        f"summary={result['summary']}",
    ]
    if result.get("review_pack"):
        lines.insert(4, f"review_pack={result['review_pack']}")
    if result.get("action_card"):
        lines.insert(5, f"action_card={result['action_card']}")
    typer.echo("\n".join(lines))


@evolution_app.command("rollback")
def evolution_rollback(
    run: str = typer.Option(..., "--run", help="Evolution run ID"),
    reason: str = typer.Option("", "--reason", help="Optional rollback reason"),
):
    """Rollback an applied evolution run from archived snapshots."""
    try:
        result = rollback_evolution_run(
            workspace=_workspace_path(),
            project_root=_project_root(),
            run_id=run,
            reason=reason,
        )
    except EvolutionRollbackError as exc:
        raise typer.BadParameter(str(exc)) from exc
    lines = [
        f"run={result['run_id']} status={result['status']}",
        f"restored_count={result['restored_count']}",
        f"skipped_count={result['skipped_count']}",
        f"rollback={result['rollback']}",
        f"summary={result['summary']}",
    ]
    typer.echo("\n".join(lines))


@evolution_app.command("trigger-scan")
def evolution_trigger_scan(
    status: str = typer.Option("active", "--status", help="Site registry status to scan; use 'all' for all sites"),
    create_runs: bool = typer.Option(True, "--create-runs/--no-create-runs", help="Create evolution run archives for triggered candidates"),
    review_gate: bool = typer.Option(False, "--review-gate/--no-review-gate", help="Create Codex-readable review cards before concrete evolution runs"),
):
    """Scan local evidence and create evolution triggers."""
    normalized_status = "" if str(status or "").strip().lower() == "all" else str(status or "").strip()
    try:
        result = scan_evolution_triggers(
            project_root=_project_root(),
            workspace=_workspace_path(),
            status=normalized_status,
            create_runs=create_runs,
            review_gate=review_gate,
        )
    except EvolutionTriggerError as exc:
        raise typer.BadParameter(str(exc)) from exc
    site_workflow = result.get("site_workflow") if isinstance(result.get("site_workflow"), dict) else {}
    target_company = result.get("target_company_intelligence") if isinstance(result.get("target_company_intelligence"), dict) else {}
    assistant_memory = result.get("assistant_router_memory_intake") if isinstance(result.get("assistant_router_memory_intake"), dict) else {}
    lines = [f"triggered={result['triggered_count']}", f"review_gate={str(review_gate).lower()}"]
    for label, group in (
        ("site_workflow", site_workflow),
        ("target_company_intelligence", target_company),
        ("application_strategy", result.get("application_strategy") if isinstance(result.get("application_strategy"), dict) else {}),
        ("assistant_router_memory_intake", assistant_memory),
    ):
        lines.append(
            f"{label}: candidate={group.get('candidate_id')} triggered={group.get('triggered_count')} "
            f"buckets={group.get('bucket_count')} sites={group.get('site_count')}"
        )
        lines.append(f"{label}: state={group.get('state_path')}")
        lines.append(f"{label}: open_candidates={group.get('open_candidates_path')}")
        for row in group.get("triggered") or []:
            phase_or_area = row.get("phase") or row.get("area") or row.get("trigger_type")
            count = row.get("phase_run_count") or row.get("job_count") or row.get("review_count") or row.get("rejected_count") or 0
            subject = row.get("site_key") or row.get("area") or row.get("candidate_id") or label
            lines.append(
                f"- {subject}:{phase_or_area} trigger={row.get('trigger_type')} "
                f"count={count} run={row.get('evolution_run_id') or '-'} review_card={row.get('evolution_review_card_id') or '-'}"
            )
    typer.echo("\n".join(lines))


@app.command("batch-list")
def batch_list(
    session: str = typer.Option("", "--session", "-s", help="Optional session ID filter"),
):
    """List open job batches."""
    store = _job_store()
    rows = store.list_batches(session_id=session or None, include_terminal=False)
    if not rows:
        typer.echo("No open batches found.")
        return
    for row in rows:
        batch_id = str(row.get("batch_id") or "")
        status = str(row.get("status") or "")
        session_id = str(row.get("session_id") or "")
        updated_at = str(row.get("updated_at") or row.get("created_at") or "")
        typer.echo(f"{batch_id}\t{status}\t{session_id}\t{updated_at}")


@app.command("batch-clear")
def batch_clear(
    session: str = typer.Option("", "--session", "-s", help="Optional session ID filter"),
):
    """Clear all open job batches by marking them cancelled."""
    store = _job_store()
    rows = store.clear_open_batches(session_id=session or None)
    if not rows:
        typer.echo("No open batches to clear.")
        return
    typer.echo(f"cleared={len(rows)}")
    for row in rows:
        typer.echo(f"{row.get('batch_id')}\t{row.get('session_id')}\t{row.get('status')}")


@app.command("batch-stop")
def batch_stop(
    session: str = typer.Option("", "--session", "-s", help="Optional session ID filter"),
):
    """Cancel open job batches and stop the workspace manager/browser runtime."""
    root = _project_root()
    workspace = _workspace_path()
    session_filter = session or None
    shutdown_error = ""
    try:
        response = shutdown_workspace_manager(
            project_root=root,
            workspace=workspace,
            cancel_open_batches=True,
            session_id=session_filter,
            wait_timeout_seconds=10.0,
        )
    except Exception as exc:
        shutdown_error = str(exc)
        response = {"ok": False, "running": False, "stopped": False, "cancelled": 0}
    response_cancelled = int(response.get("cancelled") or 0)
    stopped_manager_processes = _stop_workspace_manager_processes(project_root=root, workspace=workspace)
    stopped_cli_processes = _stop_careereng_long_task_processes()
    stopped_browser_processes = _stop_workspace_browser_processes_until_clean(workspace)
    rows = JobStore(workspace).clear_open_batches(session_id=session_filter)
    cancelled = max(response_cancelled, len(rows))
    manager_left = _list_workspace_manager_pids(project_root=root, workspace=workspace)
    if not bool(response.get("running")) and not stopped_manager_processes and not manager_left:
        status = "not_running"
    elif bool(response.get("stopped")) or stopped_manager_processes:
        status = "stopped"
    else:
        status = "shutdown_pending"
    parts = [
        f"manager={status}",
        f"cancelled={cancelled}",
        f"manager_processes_stopped={stopped_manager_processes}",
        f"cli_processes_stopped={stopped_cli_processes}",
        f"browser_processes_stopped={stopped_browser_processes}",
    ]
    if shutdown_error:
        parts.append(f"shutdown_error={shutdown_error}")
    typer.echo(" ".join(parts))


@app.command("cleanup")
def cleanup_workspace(
    days: int = typer.Option(30, "--days", min=0, help="Delete runtime artifacts older than this many days"),
    site: str = typer.Option("", "--site", help="Optional site key to limit cleanup"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview cleanup without deleting files"),
    force: bool = typer.Option(False, "--force", help="Actually delete planned files"),
    include_profile_backups: bool = typer.Option(
        False,
        "--include-profile-backups",
        help="Also include browser/user_data.backup.* files; never includes browser/user_data",
    ),
):
    """Safely clean old runtime/debug artifacts without deleting job history or login profiles."""
    workspace = _workspace_path()
    plan = build_cleanup_plan(
        workspace=workspace,
        days=days,
        site=site,
        include_profile_backups=include_profile_backups,
    )
    typer.echo(
        f"cleanup candidates={len(plan.candidates)} bytes={_format_bytes(plan.total_bytes)} "
        f"days={plan.days} site={site or 'all'}"
    )
    for candidate in plan.candidates[:200]:
        rel = candidate.path
        try:
            rel = candidate.path.relative_to(workspace)
        except ValueError:
            pass
        typer.echo(f"- {rel}\t{_format_bytes(candidate.size_bytes)}\t{candidate.reason}")
    if len(plan.candidates) > 200:
        typer.echo(f"... {len(plan.candidates) - 200} more")
    if not force:
        if not dry_run:
            typer.echo("refusing to delete without --force")
        else:
            typer.echo("dry_run=true; pass --force to delete these files")
        return
    result = execute_cleanup_plan(plan)
    typer.echo(f"deleted={result['deleted']} bytes={_format_bytes(result['deleted_bytes'])}")


@app.command("batch-apply")
def batch_apply(
    site: str = typer.Option(..., "--site", help="Site key to apply from"),
    batch: str = typer.Option("latest", "--batch", help="Job batch ID, or latest"),
    limit: int = typer.Option(3, "--limit", min=1, help="Number of jobs to apply from this site"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    apply_only: bool = typer.Option(False, "--apply-only", help="Skip session preparation and run apply directly"),
):
    """Apply the first N jobs from an existing batch without rerunning retrieval."""
    loop, _ = _build_loop()
    reply = ""
    try:
        try:
            reply = BatchApplyDebugRunner(loop.job_flow).run(
                batch_id=batch,
                site_key=site,
                limit=limit,
                session_id=session,
                turn_id=make_id("turn"),
                apply_only=apply_only,
            )
        finally:
            if "status=waiting_user" not in str(reply or ""):
                _close_loop_if_possible(loop)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(reply)


@app.command("batch-debug-create")
def batch_debug_create(
    site: str = typer.Option(..., "--site", help="Site key to isolate from"),
    batch: str = typer.Option("latest", "--batch", help="Source job batch ID, or latest"),
    job_id: str = typer.Option("", "--job-id", help="Exact job_id to isolate"),
    title: str = typer.Option("", "--title", help="Case-insensitive title substring to isolate"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
):
    """Create a one-job debug batch from an existing site batch."""
    if bool(job_id.strip()) == bool(title.strip()):
        raise typer.BadParameter("provide exactly one of --job-id or --title")
    loop, _ = _build_loop()
    try:
        try:
            debug_batch_id = BatchApplyDebugRunner(loop.job_flow).create_debug_batch(
                batch_id=batch,
                site_key=site,
                session_id=session,
                turn_id=make_id("turn"),
                job_id=job_id,
                title_contains=title,
            )
        finally:
            _close_loop_if_possible(loop)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    normalized_site_key = safe_file_stem(site)
    typer.echo(f"source_batch={batch} debug_batch={debug_batch_id} site={normalized_site_key}")
    typer.echo(f"next: python -m careereng batch-apply --site {normalized_site_key} --batch {debug_batch_id} --limit 1")


@runtime_host_app.command("serve")
def runtime_host_serve(
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to the current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
    socket_path: str = typer.Option("", "--socket-path", help="Optional Unix socket path for this host"),
):
    """Run the user-owned local runtime host for browser and phase execution."""
    root = Path(project_root).expanduser().resolve() if project_root.strip() else _project_root()
    resolved_workspace = Path(workspace).expanduser().resolve() if workspace.strip() else runtime_workspace_path(root)
    endpoint = Path(socket_path).expanduser() if socket_path.strip() else runtime_host_socket_path(resolved_workspace)
    serve_runtime_host(project_root=root, workspace=resolved_workspace, socket_path=endpoint)


@runtime_host_app.command("status")
def runtime_host_show_status(
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to the current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
):
    """Show runtime-host reachability without starting a host process."""
    root = Path(project_root).expanduser().resolve() if project_root.strip() else _project_root()
    resolved_workspace = Path(workspace).expanduser().resolve() if workspace.strip() else runtime_workspace_path(root)
    typer.echo(json.dumps(runtime_host_status(project_root=root, workspace=resolved_workspace), ensure_ascii=False, indent=2))


@runtime_host_app.command("stop")
def runtime_host_stop(
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to the current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
    cancel_open_batches: bool = typer.Option(False, "--cancel-open-batches", help="Also cancel open batches before stopping"),
):
    """Stop the user-owned local runtime host."""
    root = Path(project_root).expanduser().resolve() if project_root.strip() else _project_root()
    resolved_workspace = Path(workspace).expanduser().resolve() if workspace.strip() else runtime_workspace_path(root)
    response = runtime_host_client(project_root=root, workspace=resolved_workspace, autostart=False).shutdown(
        cancel_open_batches=cancel_open_batches,
    )
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@app.command("manager-serve", hidden=True)
def manager_serve(
    project_root: str = typer.Option(..., "--project-root", help="Project root"),
    workspace: str = typer.Option(..., "--workspace", help="Workspace path"),
    socket_path: str = typer.Option(..., "--socket-path", help="Unix socket path"),
):
    """Deprecated compatibility alias for ``runtime-host serve``."""
    serve_runtime_host(
        project_root=Path(project_root).expanduser().resolve(),
        workspace=Path(workspace).expanduser().resolve(),
        socket_path=Path(socket_path).expanduser(),
    )


@app.command("mcp-server")
def mcp_server(
    project_root: str = typer.Option("", "--project-root", help="Project root; defaults to current CareerEng project"),
    workspace: str = typer.Option("", "--workspace", help="Workspace path; defaults to configured workspace"),
    transport: str = typer.Option("stdio", "--transport", help="stdio, sse, or streamable-http"),
    mount_path: str = typer.Option("", "--mount-path", help="Optional HTTP mount path for non-stdio transports"),
):
    """Run the CareerEng MCP server for Codex or another local agent."""
    root = Path(project_root).expanduser().resolve() if str(project_root or "").strip() else _project_root()
    resolved_workspace = Path(workspace).expanduser().resolve() if str(workspace or "").strip() else runtime_workspace_path(root)
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise typer.BadParameter("transport must be one of: stdio, sse, streamable-http")
    run_mcp_server(
        project_root=root,
        workspace=resolved_workspace,
        transport=transport,  # type: ignore[arg-type]
        mount_path=str(mount_path or "").strip() or None,
    )


@site_app.command("add")
def site_add(
    name: str = typer.Argument(..., help="Company or site name"),
    url: str = typer.Option("", "--url", help="Known entry URL"),
):
    """Register or reactivate one site."""
    _, _, _, search_store, _, site_tools, locator = _build_site_services()
    turn_id = make_id("turn")
    base_url = url.strip()
    if not base_url:
        query = search_store.start_query(
            session_id="cli:site",
            turn_id=turn_id,
            user_message=f"site add {name}",
            query_spec={"mode": "site_add", "company": name},
        )
        resolved = locator.resolve_company_apply_channels(
            query_id=str(query.get("query_id") or ""),
            companies=[{"company": name, "base_url": ""}],
        )
        if resolved:
            base_url = str(resolved[0].get("base_url") or "")
    result = site_tools.handle_site_request(
        site_name=name,
        base_url=base_url,
        apply_requested=False,
        session_id="cli:site",
        turn_id=turn_id,
        source_type="manual",
    )
    typer.echo(f"registered: {result.get('site_name')} [{result.get('site_id')}] status={result.get('status')}")
    typer.echo(f"entry_url: {result.get('base_url') or '-'}")
    state = "created" if result.get("skill_template_created") else "existing"
    typer.echo(f"site_skill: {result.get('skill_path')} ({state})")
    if result.get("action_card_id"):
        typer.echo(f"action_card: {result.get('action_card_id')} {result.get('action_card_path') or ''}".rstrip())


@site_app.command("bootstrap")
def site_bootstrap(
    name: str = typer.Argument(..., help="Company or site name"),
    url: str = typer.Option("", "--url", help="Optional known entry URL"),
    session: str = typer.Option("cli:site", "--session", "-s", help="Session ID for audit events"),
):
    """Prepare a testable site AI Skill action card without running browser phases."""
    _, _, _, search_store, _, site_tools, locator = _build_site_services()
    turn_id = make_id("turn")
    try:
        result = bootstrap_site_launcher(
            site_name=name,
            base_url=url,
            session_id=session,
            turn_id=turn_id,
            search_store=search_store,
            site_tools=site_tools,
            channel_locator=locator,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"bootstrap: {result.get('site_name')} [{result.get('site_id')}] status={result.get('status')}")
    typer.echo(f"entry_url: {result.get('base_url') or '-'}")
    typer.echo(f"entry_url_source: {result.get('base_url_source') or '-'}")
    state = "created" if result.get("skill_template_created") else "existing"
    typer.echo(f"site_skill: {result.get('skill_path')} ({state})")
    if result.get("action_card_id"):
        typer.echo(f"action_card: {result.get('action_card_id')} {result.get('action_card_path') or ''}".rstrip())
        if result.get("evolution_run_id"):
            typer.echo(f"evolution_run: {result.get('evolution_run_id')}")
        if result.get("evidence_pack"):
            typer.echo(f"evidence_pack: {result.get('evidence_pack')}")
        typer.echo(f"next: {result.get('next_action')}")
    else:
        typer.echo("action_card: -")


@site_app.command("list")
def site_list(
    status: str = typer.Option("all", "--status", help="all/active/inactive"),
):
    """List site registry rows."""
    _, workspace, _, _, site_store, _, _ = _build_site_services()
    status_value = status.strip().lower()
    if status_value not in {"all", "active", "inactive"}:
        raise typer.BadParameter("--status must be all/active/inactive")
    rows = site_store.list_sites(None if status_value == "all" else status_value)
    if not rows:
        typer.echo(f"No sites found in {workspace / 'sites' / 'registry.jsonl'}")
        return
    for row in rows:
        typer.echo(
            f"{row.get('status')}\t{row.get('canonical_company')}\t[{row.get('site_key')}]\t{row.get('base_url') or '-'}"
        )


@site_app.command("deactivate")
def site_deactivate(
    name: str = typer.Argument(..., help="Company name or site key"),
):
    """Mark a site inactive without deleting local history."""
    _, _, _, _, site_store, _, _ = _build_site_services()
    try:
        row = site_store.deactivate(name)
    except KeyError as exc:
        raise typer.BadParameter(f"site not found: {name}") from exc
    typer.echo(f"deactivated: {row.get('canonical_company')} [{row.get('site_key')}]")


@site_app.command("activate")
def site_activate(
    name: str = typer.Argument(..., help="Company name or site key"),
    url: str = typer.Option("", "--url", help="Optional entry URL update"),
):
    """Reactivate a previously registered site."""
    _, _, _, _, site_store, _, _ = _build_site_services()
    try:
        row = site_store.activate(name, base_url=url)
    except KeyError as exc:
        raise typer.BadParameter(f"site not found: {name}") from exc
    typer.echo(f"activated: {row.get('canonical_company')} [{row.get('site_key')}] -> {row.get('base_url') or '-'}")


@resume_app.command("upload")
def resume_upload(
    file: str = typer.Option(..., "--file", help="Resume file path"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
):
    """Upload resume and update persona.md."""
    path = Path(file).expanduser()
    if not path.exists():
        raise typer.BadParameter(f"file not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = path.read_bytes().decode("utf-8", errors="ignore")

    loop, _ = _build_loop()
    try:
        reply = loop.process_resume_upload(session_id=session, text=text, source_name=path.name)
    finally:
        _close_loop_if_possible(loop)

    workspace = _workspace_path()
    sources = workspace / "profile" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    snapshot = sources / path.name
    try:
        snapshot.write_text(text, encoding="utf-8")
    except Exception:
        pass

    typer.echo(reply)


@resume_app.command("export-pdf")
def resume_export_pdf(
    file: str = typer.Option(..., "--file", help="Markdown resume source path"),
    output: str = typer.Option("", "--output", "-o", help="Optional output PDF path"),
    template: str = typer.Option("", "--template", help="Optional Typst template path"),
):
    """Export one Markdown resume file to PDF through Typst."""
    workspace = _workspace_path()
    try:
        result = export_resume_pdf_file(
            workspace=workspace,
            markdown_path=Path(file),
            output_path=Path(output) if output.strip() else None,
            template=template,
        )
    except ResumeExportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    typer.echo(f"template: {result.template_path}")
    typer.echo(f"typst_source: {result.typ_path}")
    typer.echo(f"pdf: {result.pdf_path}")


def _stores() -> tuple[ProfileStore, IntentStore]:
    workspace = _workspace_path()
    return ProfileStore(workspace), IntentStore(workspace)


def _router_store() -> RouterStore:
    return RouterStore(_workspace_path())


@report_app.command("list")
def report_list():
    """List pending reports."""
    profile_store, intent_store = _stores()
    rows = []
    for r in profile_store.list_reports():
        rows.append(("profile", r))
    for r in intent_store.list_reports():
        rows.append(("intent", r))

    if not rows:
        typer.echo("No reports found.")
        return

    for domain, report in rows:
        typer.echo(
            f"[{domain}] id={report.get('id')} status={report.get('status')} items={len(report.get('items') or [])}"
        )


@report_app.command("jobs")
def report_jobs(
    batch: str = typer.Option("latest", "--batch", help="Job batch ID, or latest"),
):
    """Generate a simple job batch report."""
    root = _project_root()
    workspace = _workspace_path()
    try:
        report = generate_job_batch_report(workspace=workspace, project_root=root, batch_id=batch)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    typer.echo(f"batch={report.get('batch_id')} status={report.get('status') or 'unknown'}")
    typer.echo(
        f"retrieved={int(totals.get('retrieved_count') or 0)} "
        f"submitted={int(totals.get('submitted_count') or 0)} "
        f"already_applied={int(totals.get('already_applied_count') or 0)} "
        f"new={int(totals.get('new_jobs_count') or 0)} "
        f"new_submitted={int(totals.get('new_submitted_count') or 0)} "
        f"new_filtered_out={int(totals.get('new_filtered_out_count') or 0)}"
    )
    typer.echo(f"json: {report.get('json_path')}")
    typer.echo(f"markdown: {report.get('markdown_path')}")


def _find_report(report_id: str) -> tuple[str, dict[str, Any], Any] | None:
    profile_store, intent_store = _stores()
    report = profile_store.load_report(report_id)
    if report:
        return "profile", report, profile_store
    report = intent_store.load_report(report_id)
    if report:
        return "intent", report, intent_store
    return None


@report_app.command("review")
def report_review(report_id: str = typer.Option(..., "--id", help="Report ID")):
    """Review one report, mark relevance, and optionally apply patch."""
    found = _find_report(report_id)
    if not found:
        raise typer.BadParameter(f"report not found: {report_id}")

    domain, report, store = found
    items = report.get("items") or []
    if not isinstance(items, list):
        items = []

    typer.echo(f"Reviewing report {report_id} ({domain}), items={len(items)}")
    merged_patch: dict[str, Any] = {}

    for idx, item in enumerate(items, 1):
        msg = str(item.get("message") or "")
        reason = str(item.get("reason") or "")
        patch = item.get("patch") if isinstance(item.get("patch"), dict) else {}
        typer.echo(f"\n[{idx}] message: {msg}")
        if reason:
            typer.echo(f"reason: {reason}")
        typer.echo("patch candidate:")
        typer.echo(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).strip() or "{}")
        relevant = typer.confirm("标记为相关？")
        item["user_relevant"] = bool(relevant)
        if relevant and patch:
            _merge(merged_patch, patch)
            store.update_event(str(item.get("event_id") or ""), status="user_related")
        else:
            store.update_event(str(item.get("event_id") or ""), status="user_irrelevant")

    report["status"] = "reviewed"
    report["items"] = items

    typer.echo("\n合并后的 patch:")
    typer.echo(yaml.safe_dump(merged_patch, allow_unicode=True, sort_keys=False).strip() or "{}")

    apply_update = bool(merged_patch) and typer.confirm("是否写回到文档？")
    report["apply_decision"] = "yes" if apply_update else "no"
    report["applied"] = apply_update

    if apply_update:
        store.apply_patch(merged_patch, reason=f"report_review:{report_id}")

    store.save_report(report)
    typer.echo("Review completed.")


@app.command("viewreport")
def view_report(report_id: str = typer.Option(..., "--id", help="Report ID")):
    """Alias of `report review`."""
    report_review(report_id)


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
    store = _router_store()
    event = store.find_event(event_id)
    if not event:
        raise typer.BadParameter(f"route event not found: {event_id}")

    val = correct.strip().lower()
    if val in {"yes", "y", "true", "1"}:
        is_correct = True
    elif val in {"no", "n", "false", "0"}:
        is_correct = False
    else:
        raise typer.BadParameter("--correct must be yes/no")

    route = expected_route.strip().lower()
    if route and route not in {"chat", "search", "site"}:
        raise typer.BadParameter("--expected-route must be chat/search/site")

    row = store.append_feedback(
        {
            "route_event_id": event_id,
            "session_id": str(event.get("session_id") or ""),
            "turn_id": str(event.get("turn_id") or ""),
            "is_correct": is_correct,
            "expected_route": route,
            "comment": comment,
            "reviewer_type": reviewer_type,
            "reviewer_name": reviewer_name,
        }
    )
    typer.echo(
        f"feedback saved: id={row.get('feedback_id')} event={event_id} correct={is_correct} expected={route or '-'}"
    )


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base
