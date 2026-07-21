"""CLI adapter for application summaries, reports, and batch commands."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import typer
import yaml

from careereng.career.applications import (
    build_application_summary,
    generate_job_batch_report,
    inspect_history_repairs,
    save_application_summary,
    save_history_repair_plan,
)
from careereng.career.applications.job_store import JobStore
from careereng.career.applications.batch_debug import BatchApplyDebugRunner
from careereng.career.profile.intent_store import IntentStore
from careereng.career.profile.store import ProfileStore
from careereng.career.applications.site_bootstrap import bootstrap_site
from careereng.adapters.bootstrap import build_loop, build_site_services
from careereng.adapters.host.workspace_manager import dispatch_manager_message, start_manager_jobs_batch
from careereng.platform.runtime_host import runtime_host_client
from careereng.adapters.cli.batch_monitor import (
    TERMINAL_BATCH_STATUSES,
    dispatch_with_phase_progress,
    emit_new_phase_events,
    format_batch_summary,
    new_batch_baseline,
)
from careereng.config.loader import load_config
from careereng.utils import make_id, safe_file_stem


APPLICATION_SUMMARY_REPAIR_THRESHOLD = 25
application_summary_app = typer.Typer(help="Application lifecycle summary commands")
report_app = typer.Typer(help="Report review commands")
site_app = typer.Typer(help="Site registry commands")
jobs_app = typer.Typer(help="Registered-site job retrieval/apply commands")
application_cli_app = typer.Typer(help="Application commands")
application_cli_app.add_typer(application_summary_app, name="application-summary")
application_cli_app.add_typer(report_app, name="report")
application_cli_app.add_typer(site_app, name="site")
application_cli_app.add_typer(jobs_app, name="jobs")

JOBS_APPLY_MESSAGE = "检索投递已注册的公司"
JOBS_REVIEW_STATUS_MESSAGE = "检查已投递岗位状态"


def _project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "careereng").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def _workspace_path() -> Path:
    workspace = load_config(_project_root()).paths.workspace_path(_project_root())
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _format_int(value: object) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _build_site_services():
    return build_site_services(project_root=_project_root(), workspace=_workspace_path())


def _job_store() -> JobStore:
    return JobStore(_workspace_path())


def _close_loop(loop: object) -> None:
    close = getattr(loop, "close", None)
    if callable(close):
        close()


def _run_jobs_batch(*, operation: str, apply_requested: bool, message: str, session: str) -> str:
    """Start an existing manager batch and present its persisted progress."""
    root = _project_root()
    workspace = _workspace_path()
    baseline = new_batch_baseline(workspace=workspace, session_id=session)
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
    state: dict[str, Any] = {"batch_id": batch_id, "turn_id": str(response.get("turn_id") or "")}
    typer.echo(str(response.get("reply") or f"batch={batch_id} status=running"))
    store = _job_store()
    try:
        while True:
            emit_new_phase_events(
                workspace=workspace,
                session_id=session,
                baseline_batch_ids=baseline,
                state=state,
                emit=typer.echo,
            )
            try:
                batch = store.load_batch(batch_id)
            except (FileNotFoundError, KeyError):
                time.sleep(0.75)
                continue
            if str(batch.get("status") or "") in TERMINAL_BATCH_STATUSES:
                emit_new_phase_events(
                    workspace=workspace,
                    session_id=session,
                    baseline_batch_ids=baseline,
                    state=state,
                    emit=typer.echo,
                )
                return format_batch_summary(batch, workspace=workspace)
            time.sleep(0.75)
    except KeyboardInterrupt:
        return f"batch={batch_id} status=running\n后台批次仍可能继续运行；可用 batch-list 查看状态。"


def _dispatch_message_with_progress(*, message: str, session: str) -> str:
    """Dispatch a generic manager turn while showing any resulting phase events."""
    root = _project_root()
    workspace = _workspace_path()
    return dispatch_with_phase_progress(
        dispatch=lambda: dispatch_manager_message(
            project_root=root, workspace=workspace, session_id=session, message=message
        ),
        workspace=workspace,
        session_id=session,
        emit=typer.echo,
    )


@application_summary_app.command("build")
def application_summary_build(
    since: str = typer.Option("2026-04-01", "--since", help="Only include application data on or after this date"),
    all_time: bool = typer.Option(False, "--all-time", help="Include all historical application data"),
) -> None:
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
        f"since={filters.get('since') or 'all'} sites={_format_int(source.get('site_count'))} "
        f"jobs={_format_int(totals.get('history_jobs'))} submitted={_format_int(totals.get('submitted'))} "
        f"active={_format_int(totals.get('active'))} rejected={_format_int(totals.get('rejected'))} "
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
    apply_repairs: bool = typer.Option(False, "--apply", help="Apply safe history repairs"),
) -> None:
    """Inspect history data quality and optionally apply safe repairs."""
    root = _project_root()
    workspace = _workspace_path()
    plan = inspect_history_repairs(
        workspace=workspace, project_root=root, since=None if all_time else since, apply=apply_repairs
    )
    path = save_history_repair_plan(plan, workspace=workspace)
    source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
    filters = source.get("filters") if isinstance(source.get("filters"), dict) else {}
    totals = plan.get("totals") if isinstance(plan.get("totals"), dict) else {}
    category_counts = plan.get("category_counts") if isinstance(plan.get("category_counts"), dict) else {}
    typer.echo(
        "history_repair: "
        f"mode={plan.get('mode') or 'dry_run'} since={filters.get('since') or 'all'} "
        f"issues={_format_int(totals.get('issue_count'))} "
        f"safe_repairable={_format_int(totals.get('safe_repairable_count'))} "
        f"applied={_format_int(totals.get('applied_count'))}"
    )
    for key in sorted(category_counts):
        typer.echo(f"- {key}: {_format_int(category_counts.get(key))}")
    typer.echo(f"path: {path}")


def _stores() -> tuple[ProfileStore, IntentStore]:
    workspace = _workspace_path()
    return ProfileStore(workspace), IntentStore(workspace)


@report_app.command("list")
def report_list() -> None:
    """List pending reports."""
    profile_store, intent_store = _stores()
    rows = [("profile", row) for row in profile_store.list_reports()]
    rows.extend(("intent", row) for row in intent_store.list_reports())
    if not rows:
        typer.echo("No reports found.")
        return
    for domain, report in rows:
        typer.echo(f"[{domain}] id={report.get('id')} status={report.get('status')} items={len(report.get('items') or [])}")


@report_app.command("jobs")
def report_jobs(batch: str = typer.Option("latest", "--batch", help="Job batch ID, or latest")) -> None:
    """Generate a simple job batch report."""
    try:
        report = generate_job_batch_report(workspace=_workspace_path(), project_root=_project_root(), batch_id=batch)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    typer.echo(f"batch={report.get('batch_id')} status={report.get('status') or 'unknown'}")
    typer.echo(
        f"retrieved={int(totals.get('retrieved_count') or 0)} submitted={int(totals.get('submitted_count') or 0)} "
        f"already_applied={int(totals.get('already_applied_count') or 0)} new={int(totals.get('new_jobs_count') or 0)} "
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


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


@report_app.command("review")
def report_review(report_id: str = typer.Option(..., "--id", help="Report ID")) -> None:
    """Review one report, mark relevance, and optionally apply its patch."""
    found = _find_report(report_id)
    if not found:
        raise typer.BadParameter(f"report not found: {report_id}")
    domain, report, store = found
    items = report.get("items") if isinstance(report.get("items"), list) else []
    typer.echo(f"Reviewing report {report_id} ({domain}), items={len(items)}")
    merged_patch: dict[str, Any] = {}
    for index, item in enumerate(items, 1):
        message = str(item.get("message") or "")
        reason = str(item.get("reason") or "")
        patch = item.get("patch") if isinstance(item.get("patch"), dict) else {}
        typer.echo(f"\n[{index}] message: {message}")
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


@application_cli_app.command("viewreport")
def view_report(report_id: str = typer.Option(..., "--id", help="Report ID")) -> None:
    """Alias of `report review`."""
    report_review(report_id)


@site_app.command("add")
def site_add(name: str = typer.Argument(..., help="Company or site name"), url: str = typer.Option("", "--url")) -> None:
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
            query_id=str(query.get("query_id") or ""), companies=[{"company": name, "base_url": ""}]
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
    typer.echo(f"site_skill: {result.get('skill_path')} ({'created' if result.get('skill_template_created') else 'existing'})")
    if result.get("action_card_id"):
        typer.echo(f"action_card: {result.get('action_card_id')} {result.get('action_card_path') or ''}".rstrip())


@site_app.command("bootstrap")
def site_bootstrap(
    name: str = typer.Argument(..., help="Company or site name"),
    url: str = typer.Option("", "--url", help="Optional known entry URL"),
    session: str = typer.Option("cli:site", "--session", "-s", help="Session ID for audit events"),
) -> None:
    """Prepare a testable site AI Skill action card without browser phases."""
    _, _, _, search_store, _, site_tools, locator = _build_site_services()
    try:
        result = bootstrap_site(
            site_name=name,
            base_url=url,
            session_id=session,
            turn_id=make_id("turn"),
            search_store=search_store,
            site_tools=site_tools,
            channel_locator=locator,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"bootstrap: {result.get('site_name')} [{result.get('site_id')}] status={result.get('status')}")
    typer.echo(f"entry_url: {result.get('base_url') or '-'}")
    typer.echo(f"entry_url_source: {result.get('base_url_source') or '-'}")
    typer.echo(f"site_skill: {result.get('skill_path')} ({'created' if result.get('skill_template_created') else 'existing'})")
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
def site_list(status: str = typer.Option("all", "--status", help="all/active/inactive")) -> None:
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
        typer.echo(f"{row.get('status')}\t{row.get('canonical_company')}\t[{row.get('site_key')}]\t{row.get('base_url') or '-'}")


@site_app.command("deactivate")
def site_deactivate(name: str = typer.Argument(..., help="Company name or site key")) -> None:
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
) -> None:
    """Reactivate a previously registered site."""
    _, _, _, _, site_store, _, _ = _build_site_services()
    try:
        row = site_store.activate(name, base_url=url)
    except KeyError as exc:
        raise typer.BadParameter(f"site not found: {name}") from exc
    typer.echo(f"activated: {row.get('canonical_company')} [{row.get('site_key')}] -> {row.get('base_url') or '-'}")


@application_cli_app.command("batch-list")
def batch_list(session: str = typer.Option("", "--session", "-s", help="Optional session ID filter")) -> None:
    """List open job batches."""
    rows = _job_store().list_batches(session_id=session or None, include_terminal=False)
    if not rows:
        typer.echo("No open batches found.")
        return
    for row in rows:
        typer.echo(
            f"{row.get('batch_id') or ''}\t{row.get('status') or ''}\t"
            f"{row.get('session_id') or ''}\t{row.get('updated_at') or row.get('created_at') or ''}"
        )


@application_cli_app.command("batch-clear")
def batch_clear(session: str = typer.Option("", "--session", "-s", help="Optional session ID filter")) -> None:
    """Clear all open job batches by marking them cancelled."""
    rows = _job_store().clear_open_batches(session_id=session or None)
    if not rows:
        typer.echo("No open batches to clear.")
        return
    typer.echo(f"cleared={len(rows)}")
    for row in rows:
        typer.echo(f"{row.get('batch_id')}\t{row.get('session_id')}\t{row.get('status')}")


@application_cli_app.command("batch-cancel")
def batch_cancel(batch: str = typer.Option(..., "--batch", help="Exact job batch ID to cancel")) -> None:
    """Cancel one batch without clearing other open batches."""
    response = runtime_host_client(
        project_root=_project_root(), workspace=_workspace_path(), autostart=False
    ).request("cancel_jobs_batch", {"batch_id": batch, "reason": "cli_batch_cancel"})
    if not bool(response.get("ok")):
        raise typer.BadParameter(str(response.get("error") or "batch cancellation failed"))
    saved = response.get("batch") if isinstance(response.get("batch"), dict) else {}
    typer.echo(f"batch={saved.get('batch_id') or batch} status={saved.get('status') or 'cancelled'}")


@application_cli_app.command("batch-stop")
def batch_stop(session: str = typer.Option("", "--session", "-s", help="Optional session ID filter")) -> None:
    """Cancel open batches and request a clean runtime-host shutdown."""
    root = _project_root()
    workspace = _workspace_path()
    session_filter = session or None
    try:
        response = runtime_host_client(project_root=root, workspace=workspace, autostart=False).shutdown(
            cancel_open_batches=True
        )
    except Exception as exc:
        response = {"ok": False, "running": False, "stopped": False, "error": str(exc)}
    rows = JobStore(workspace).clear_open_batches(session_id=session_filter)
    state = "stopped" if response.get("stopped") else "not_running" if not response.get("running") else "shutdown_pending"
    parts = [f"manager={state}", f"cancelled={max(int(response.get('cancelled') or 0), len(rows))}"]
    if response.get("error"):
        parts.append(f"shutdown_error={response['error']}")
    typer.echo(" ".join(parts))


@application_cli_app.command("batch-apply")
def batch_apply(
    site: str = typer.Option(..., "--site", help="Site key to apply from"),
    batch: str = typer.Option("latest", "--batch", help="Job batch ID, or latest"),
    limit: int = typer.Option(3, "--limit", min=1, help="Number of jobs to apply from this site"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    apply_only: bool = typer.Option(False, "--apply-only", help="Skip session preparation and run apply directly"),
) -> None:
    """Apply the first N jobs from an existing batch without retrieval."""
    loop, _ = build_loop(project_root=_project_root(), workspace=_workspace_path())
    reply = ""
    try:
        reply = BatchApplyDebugRunner(loop.job_flow).run(
            batch_id=batch,
            site_key=site,
            limit=limit,
            session_id=session,
            turn_id=make_id("turn"),
            apply_only=apply_only,
        )
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        if "status=waiting_user" not in str(reply or ""):
            _close_loop(loop)
    typer.echo(reply)


@application_cli_app.command("batch-debug-create")
def batch_debug_create(
    site: str = typer.Option(..., "--site", help="Site key to isolate from"),
    batch: str = typer.Option("latest", "--batch", help="Source job batch ID, or latest"),
    job_id: str = typer.Option("", "--job-id", help="Exact job_id to isolate"),
    title: str = typer.Option("", "--title", help="Case-insensitive title substring to isolate"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
) -> None:
    """Create a one-job debug batch from an existing site batch."""
    if bool(job_id.strip()) == bool(title.strip()):
        raise typer.BadParameter("provide exactly one of --job-id or --title")
    loop, _ = build_loop(project_root=_project_root(), workspace=_workspace_path())
    try:
        debug_batch_id = BatchApplyDebugRunner(loop.job_flow).create_debug_batch(
            batch_id=batch,
            site_key=site,
            session_id=session,
            turn_id=make_id("turn"),
            job_id=job_id,
            title_contains=title,
        )
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        _close_loop(loop)
    normalized_site_key = safe_file_stem(site)
    typer.echo(f"source_batch={batch} debug_batch={debug_batch_id} site={normalized_site_key}")
    typer.echo(f"next: python -m careereng batch-apply --site {normalized_site_key} --batch {debug_batch_id} --limit 1")


@jobs_app.command("apply")
def jobs_apply(
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    message: str = typer.Option(JOBS_APPLY_MESSAGE, "--message", "-m", help="Registered-sites retrieval/apply prompt"),
) -> None:
    """Retrieve and apply jobs for active registered sites."""
    typer.echo(_run_jobs_batch(operation="job_search", apply_requested=True, message=message, session=session))


@jobs_app.command("review-status")
def jobs_review_status(
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    message: str = typer.Option(JOBS_REVIEW_STATUS_MESSAGE, "--message", "-m", help="Application status review prompt"),
) -> None:
    """Review submitted application statuses for active registered sites."""
    typer.echo(
        _run_jobs_batch(
            operation="application_status_review", apply_requested=False, message=message, session=session
        )
    )


@application_cli_app.command("run")
def run(
    message: str = typer.Option(..., "--message", "-m", help="Message to send"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
) -> None:
    """Run one generic CareerEng manager turn."""
    typer.echo(_dispatch_message_with_progress(message=message, session=session))
