"""CLI adapter for profile, resume, and career-memory capabilities."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from careereng.adapters.bootstrap import build_loop, project_root_from_cwd, workspace_path
from careereng.adapters.host.workspace_manager import dispatch_manager_message
from careereng.adapters.cli.batch_monitor import dispatch_with_phase_progress
from careereng.career.memory import (
    CareerMemoryError,
    import_memory_candidates,
    list_memory_units,
    promote_assistant_signals,
    show_memory_unit,
)
from careereng.career.resume.export import ResumeExportError, export_resume_pdf


profile_app = typer.Typer(help="Profile/persona commands")
resume_app = typer.Typer(help="Resume commands")
career_memory_app = typer.Typer(help="Career memory commands")
profile_app.add_typer(resume_app, name="resume")
profile_app.add_typer(career_memory_app, name="career-memory")

PROFILE_GENERATE_MESSAGE = "请根据当前 workspace 中已有的简历、profile sources 和对话信息，生成或更新用户画像 persona.md。"


def _workspace_path() -> Path:
    return workspace_path(project_root_from_cwd())


def _close_loop(loop) -> None:
    close = getattr(loop, "close", None)
    if callable(close):
        close()


@resume_app.command("upload")
def resume_upload(
    file: str = typer.Option(..., "--file"),
    session: str = typer.Option("cli:default", "--session", "-s"),
) -> None:
    """Upload resume and update persona.md."""
    path = Path(file).expanduser()
    if not path.exists():
        raise typer.BadParameter(f"file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = path.read_bytes().decode("utf-8", errors="ignore")
    loop, _ = build_loop(project_root=project_root_from_cwd(), workspace=_workspace_path())
    try:
        reply = loop.process_resume_upload(session_id=session, text=text, source_name=path.name)
    finally:
        _close_loop(loop)
    try:
        source = _workspace_path() / "profile" / "sources" / path.name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text, encoding="utf-8")
    except OSError:
        pass
    typer.echo(reply)


@resume_app.command("export-pdf")
def resume_export_pdf(
    file: str = typer.Option(..., "--file"),
    output: str = typer.Option("", "--output", "-o"),
    template: str = typer.Option("", "--template"),
) -> None:
    """Export one Markdown resume file to PDF through Typst."""
    try:
        result = export_resume_pdf(
            workspace=_workspace_path(),
            markdown_path=Path(file),
            output_path=Path(output) if output.strip() else None,
            template=template,
        )
    except ResumeExportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"template: {result.template_path}\ntypst_source: {result.typ_path}\npdf: {result.pdf_path}")


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


@profile_app.command("generate")
def profile_generate(
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    message: str = typer.Option(PROFILE_GENERATE_MESSAGE, "--message", "-m", help="Profile generation prompt"),
) -> None:
    """Generate or update persona.md through the normal agent flow."""
    root = project_root_from_cwd()
    workspace = _workspace_path()
    reply = dispatch_with_phase_progress(
        dispatch=lambda: dispatch_manager_message(
            project_root=root, workspace=workspace, session_id=session, message=message
        ),
        workspace=workspace,
        session_id=session,
        emit=typer.echo,
    )
    typer.echo(reply)


@career_memory_app.command("promote")
def career_memory_promote(
    limit: int = typer.Option(0, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    result = promote_assistant_signals(workspace=_workspace_path(), limit=limit or None)
    if json_output:
        _emit(result)
        return
    typer.echo(
        f"career-memory promoted created={result.get('created')} "
        f"skipped_existing={result.get('skipped_existing')} scanned={result.get('scanned')}"
    )


@career_memory_app.command("import-candidates")
def career_memory_import_candidates(
    input_file: Path = typer.Argument(...),
    source_limit: int = typer.Option(0, "--source-limit"),
    source_thread: str = typer.Option("", "--source-thread"),
    source_client: str = typer.Option("", "--source-client"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        result = import_memory_candidates(workspace=_workspace_path(), input_path=input_file, source_limit=source_limit, source_thread=source_thread, source_client=source_client)
    except CareerMemoryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _emit(result)
        return
    typer.echo(
        f"career-memory imported created={result.get('created')} "
        f"skipped_existing={result.get('skipped_existing')} read={result.get('read')}"
    )


@career_memory_app.command("list")
def career_memory_list(
    category: str = typer.Option("", "--category"),
    status: str = typer.Option("", "--status"),
    limit: int = typer.Option(20, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    rows = list_memory_units(workspace=_workspace_path(), category=category, status=status, limit=limit)
    if json_output:
        _emit(rows)
        return
    for row in rows:
        typer.echo(f"- {row.get('memory_id')} [{row.get('category')}/{row.get('status')}] {row.get('summary') or ''}")


@career_memory_app.command("show")
def career_memory_show(
    memory_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        row = show_memory_unit(workspace=_workspace_path(), memory_id=memory_id)
    except CareerMemoryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _emit(row)
        return
    typer.echo(f"{row.get('memory_id')} [{row.get('category')}/{row.get('status')}]\n{row.get('summary') or ''}")
