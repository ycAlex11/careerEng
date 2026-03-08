"""CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
import yaml

from careereng.agent.channel_locator import ChannelLocator
from careereng.agent.loop import AgentLoop
from careereng.config.loader import load_auth, load_config
from careereng.providers import create_provider
from careereng.providers.base import ProviderError, StructuredOutputResult
from careereng.storage.intent_store import IntentStore
from careereng.storage.profile_store import ProfileStore
from careereng.storage.router_store import RouterStore
from careereng.storage.search_store import SearchStore
from careereng.storage.site_store import SiteStore
from careereng.tools.playwright_tools import PlaywrightTools
from careereng.tools.site_tools import SiteTools
from careereng.utils import make_id
from careereng.workspace_bootstrap import bootstrap_workspace

app = typer.Typer(help="CareerEng CLI")
report_app = typer.Typer(help="Report review commands")
resume_app = typer.Typer(help="Resume commands")
route_app = typer.Typer(help="Route feedback commands")
site_app = typer.Typer(help="Site registry commands")
app.add_typer(report_app, name="report")
app.add_typer(resume_app, name="resume")
app.add_typer(route_app, name="route")
app.add_typer(site_app, name="site")


class _FallbackProvider:
    def __init__(self, error: str):
        self.error = error

    def chat(self, messages, *, model):
        return f"Provider not configured: {self.error}"

    def chat_json(self, messages, *, model, schema=None, schema_name="response", json_mode="auto"):
        return StructuredOutputResult(
            data={},
            raw=f"Provider not configured: {self.error}",
            mode="error",
            used_fallback=True,
        )


def _project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "careereng").exists():
        return cwd
    return Path(__file__).resolve().parents[2]


def _workspace_path() -> Path:
    config = load_config(_project_root())
    path = config.paths.workspace_path(_project_root())
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_site_services() -> tuple[Path, Path, Any, SearchStore, SiteStore, SiteTools, ChannelLocator]:
    root = _project_root()
    config = load_config(root)
    workspace = config.paths.workspace_path(root)
    workspace.mkdir(parents=True, exist_ok=True)
    site_store = SiteStore(workspace)
    search_store = SearchStore(workspace)
    site_tools = SiteTools(
        site_store,
        PlaywrightTools(
            headless=config.browser.headless,
            timeout_ms=config.browser.timeout_ms,
            slow_mo_ms=config.browser.slow_mo_ms,
        ),
    )
    locator = ChannelLocator(site_tools=site_tools, search_store=search_store)
    return root, workspace, config, search_store, site_store, site_tools, locator


def _build_loop() -> tuple[AgentLoop, Any]:
    root = _project_root()
    config = load_config(root)
    auth = load_auth(root)
    try:
        _, provider = create_provider(config, auth)
    except ProviderError as exc:
        provider = _FallbackProvider(str(exc))

    workspace = config.paths.workspace_path(root)
    workspace.mkdir(parents=True, exist_ok=True)
    site_store = SiteStore(workspace)
    site_tools = SiteTools(
        site_store,
        PlaywrightTools(
            headless=config.browser.headless,
            timeout_ms=config.browser.timeout_ms,
            slow_mo_ms=config.browser.slow_mo_ms,
        ),
    )
    loop = AgentLoop(
        project_root=root,
        workspace=workspace,
        provider=provider,
        model=config.agent.default_model,
        max_history_messages=config.agent.max_history_messages,
        related_history_k=config.agent.related_history_k,
        relatedness_threshold=config.agent.relatedness_threshold,
        router_confidence_threshold=config.agent.router_confidence_threshold,
        router_log_enabled=config.agent.router_log_enabled,
        search_company_top_k=config.agent.search_company_top_k,
        site_parallelism=config.agent.site_parallelism,
        site_tools=site_tools,
    )
    return loop, config


@app.command()
def onboard():
    """Create the editable workspace scaffold."""
    workspace = _workspace_path()
    rows = bootstrap_workspace(workspace)
    created = sum(1 for row in rows if row.get("status") == "created")
    existing = len(rows) - created

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
    loop, _ = _build_loop()
    reply = loop.process_message(session, message)
    typer.echo(reply)


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
    reply = loop.process_resume_upload(session_id=session, text=text, source_name=path.name)

    workspace = _workspace_path()
    sources = workspace / "profile" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    snapshot = sources / path.name
    try:
        snapshot.write_text(text, encoding="utf-8")
    except Exception:
        pass

    typer.echo(reply)


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
