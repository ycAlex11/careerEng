"""CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import yaml

from careereng.agent.loop import AgentLoop
from careereng.config.loader import load_auth, load_config
from careereng.providers import create_provider
from careereng.providers.base import ProviderError
from careereng.storage.intent_store import IntentStore
from careereng.storage.profile_store import ProfileStore
from careereng.storage.site_store import SiteStore
from careereng.tools.playwright_tools import PlaywrightTools
from careereng.tools.site_tools import SiteTools

app = typer.Typer(help="CareerEng CLI")
report_app = typer.Typer(help="Report review commands")
resume_app = typer.Typer(help="Resume commands")
app.add_typer(report_app, name="report")
app.add_typer(resume_app, name="resume")


class _FallbackProvider:
    def __init__(self, error: str):
        self.error = error

    def chat(self, messages, *, model):
        return f"Provider not configured: {self.error}"



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
        PlaywrightTools(headless=config.browser.headless, timeout_ms=config.browser.timeout_ms),
    )
    loop = AgentLoop(
        project_root=root,
        workspace=workspace,
        provider=provider,
        model=config.agent.default_model,
        max_history_messages=config.agent.max_history_messages,
        related_history_k=config.agent.related_history_k,
        relatedness_threshold=config.agent.relatedness_threshold,
        site_tools=site_tools,
    )
    return loop, config


@app.command()
def run(
    message: str = typer.Option(..., "--message", "-m", help="Message to send"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
):
    """Run one chat turn."""
    loop, _ = _build_loop()
    reply = loop.process_message(session, message)
    typer.echo(reply)


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


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base
