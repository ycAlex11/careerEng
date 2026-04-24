"""CLI commands."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import typer
import yaml
from careereng.config.loader import ensure_files
from careereng.core.runtime import build_loop as runtime_build_loop
from careereng.core.runtime import build_site_services as runtime_build_site_services
from careereng.core.runtime import project_root_from_cwd, workspace_path as runtime_workspace_path
from careereng.core.workspace_bootstrap import bootstrap_workspace
from careereng.core.workspace_manager import dispatch_manager_message, serve_workspace_manager
from careereng.resume.export import ResumeExportError, export_resume_pdf as export_resume_pdf_file
from careereng.reporting.job_report import generate_job_batch_report
from careereng.storage.job_store import JobStore
from careereng.storage.jsonl import JSONLStore
from careereng.storage.intent_store import IntentStore
from careereng.storage.profile_store import ProfileStore
from careereng.storage.router_store import RouterStore
from careereng.tools.batch_apply_debug import BatchApplyDebugRunner
from careereng.utils import make_id, safe_file_stem

app = typer.Typer(help="CareerEng CLI")
report_app = typer.Typer(help="Report review commands")
resume_app = typer.Typer(help="Resume commands")
route_app = typer.Typer(help="Route feedback commands")
site_app = typer.Typer(help="Site registry commands")
app.add_typer(report_app, name="report")
app.add_typer(resume_app, name="resume")
app.add_typer(route_app, name="route")
app.add_typer(site_app, name="site")


def _project_root() -> Path:
    return project_root_from_cwd()


def _workspace_path() -> Path:
    return runtime_workspace_path(_project_root())


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
) -> None:
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
        return
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
    reply = str(result.get("reply") or "")
    typer.echo(reply)


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
            loop.close()
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
            loop.close()
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    normalized_site_key = safe_file_stem(site)
    typer.echo(f"source_batch={batch} debug_batch={debug_batch_id} site={normalized_site_key}")
    typer.echo(f"next: python -m careereng batch-apply --site {normalized_site_key} --batch {debug_batch_id} --limit 1")


@app.command("manager-serve", hidden=True)
def manager_serve(
    project_root: str = typer.Option(..., "--project-root", help="Project root"),
    workspace: str = typer.Option(..., "--workspace", help="Workspace path"),
    socket_path: str = typer.Option(..., "--socket-path", help="Unix socket path"),
):
    """Run the hidden workspace manager server."""
    serve_workspace_manager(
        project_root=Path(project_root).expanduser().resolve(),
        workspace=Path(workspace).expanduser().resolve(),
        socket_path=Path(socket_path).expanduser(),
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
        loop.close()

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
        f"already_applied={int(totals.get('already_applied_count') or 0)}"
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
