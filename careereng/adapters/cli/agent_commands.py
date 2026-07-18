"""CLI adapter for assistant bridge, external-agent bridge, and MCP hosting."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from careereng.adapters.assistant_bridge import AssistantThreadStateStore, ingest_assistant_message
from careereng.adapters.assistant_bridge.context import build_assistant_context_pack
from careereng.adapters.assistant_bridge.intake_state import save_recent_intake_state
from careereng.adapters.host.workspace_manager import (
    call_agent_bridge_browser_tool,
    call_agent_bridge_state_tool,
    call_browser_handoff_tool,
    list_agent_bridge_browser_tools,
    list_agent_bridge_state_tools,
    list_browser_handoff_tools,
)
from careereng.adapters.mcp import run_mcp_server
from careereng.career.memory import CareerMemoryError, import_memory_candidates
from careereng.config.loader import load_config
from careereng.adapters.external_agents.contracts import AGENT_BRIDGE_STATUS


agent_bridge_app = typer.Typer(help="External agent bridge commands")
assistant_app = typer.Typer(help="External AI assistant bridge commands")
browser_handoff_app = typer.Typer(help="Legacy external-agent browser aliases")
agent_app = typer.Typer(help="External agent and MCP commands")
agent_app.add_typer(assistant_app, name="assistant")
agent_app.add_typer(agent_bridge_app, name="agent-bridge")
agent_app.add_typer(browser_handoff_app, name="browser-handoff")


def _project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "careereng").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def _workspace_path() -> Path:
    workspace = load_config(_project_root()).paths.workspace_path(_project_root())
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


@assistant_app.command("ingest")
def assistant_ingest(
    message: str = typer.Option(..., "--message", "-m", help="Assistant-side user message to classify and store"),
    client: str = typer.Option("codex", "--client", help="External assistant client name"),
    thread: str = typer.Option("default", "--thread", help="External assistant thread/conversation ID"),
    session: str = typer.Option("", "--session", "-s", help="Optional CareerEng session ID"),
    processor: str = typer.Option("local", "--processor", help="Processor adapter backend"),
) -> None:
    """Classify and persist an external assistant message for CareerEng."""
    _emit(
        ingest_assistant_message(
            workspace=_workspace_path(),
            message=message,
            client=client,
            thread_id=thread,
            session_id=session,
            processor_backend=processor,
        )
    )


@assistant_app.command("context")
def assistant_context(
    recent_limit: int = typer.Option(8, "--recent-limit", help="Recent rows/files per context section"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
) -> None:
    """Build the assistant-readable CareerEng context pack."""
    result = build_assistant_context_pack(
        project_root=_project_root(),
        workspace=_workspace_path(),
        recent_limit=recent_limit,
    )
    if json_output:
        _emit(result)
        return
    typer.echo(f"assistant_context={result.get('path')}")


@assistant_app.command("state")
def assistant_state(
    client: str = typer.Option("codex", "--client", help="External assistant client name"),
    thread: str = typer.Option("", "--thread", help="Optional external assistant thread/conversation ID"),
) -> None:
    """Show assistant bridge thread scope state."""
    store = AssistantThreadStateStore(_workspace_path())
    _emit(store.get(client=client, thread_id=thread) if thread else store.load())


@assistant_app.command("end")
def assistant_end(
    client: str = typer.Option("codex", "--client", help="External assistant client name"),
    thread: str = typer.Option("default", "--thread", help="External assistant thread/conversation ID"),
) -> None:
    """Close an active assistant bridge career scope."""
    _emit(AssistantThreadStateStore(_workspace_path()).close_scope(client=client, thread_id=thread))


def _import_candidates(
    *,
    input_file: Path,
    source_limit: int,
    source_thread: str,
    source_client: str,
) -> dict:
    try:
        return import_memory_candidates(
            workspace=_workspace_path(),
            input_path=input_file,
            source_limit=source_limit,
            source_thread=source_thread,
            source_client=source_client,
        )
    except CareerMemoryError as exc:
        raise typer.BadParameter(str(exc)) from exc


@assistant_app.command("import-candidates")
def assistant_import_candidates(
    input_file: Path = typer.Argument(..., help="JSON or JSONL file of assistant-curated memory candidates"),
    source_limit: int = typer.Option(0, "--source-limit", help="Number of recent assistant messages summarized"),
    source_thread: str = typer.Option("codex-current", "--source-thread", help="External assistant thread ID"),
    source_client: str = typer.Option("codex", "--source-client", help="External assistant client name"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
) -> None:
    """Import assistant-curated recent conversation candidates into career memory."""
    result = _import_candidates(
        input_file=input_file,
        source_limit=source_limit,
        source_thread=source_thread,
        source_client=source_client,
    )
    if json_output:
        _emit(result)
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
    source_thread: str = typer.Option("codex-current", "--source-thread", help="External assistant thread ID"),
    source_client: str = typer.Option("codex", "--source-client", help="External assistant client name"),
    recent_limit: int = typer.Option(8, "--context-recent-limit", help="Recent rows/files per context section"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
) -> None:
    """Import recent conversation candidates and refresh assistant context."""
    result = _import_candidates(
        input_file=input_file,
        source_limit=limit,
        source_thread=source_thread,
        source_client=source_client,
    )
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
    payload = {"import": result, "intake_state": state, "assistant_context": context_result}
    if json_output:
        _emit(payload)
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


def _json_args(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--args must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--args must be a JSON object")
    return parsed


def _emit_tools(
    *,
    site: str,
    phase: str,
    json_output: bool,
    legacy: bool = False,
    state: bool = False,
) -> None:
    if legacy:
        response = list_browser_handoff_tools(project_root=_project_root(), workspace=_workspace_path(), site_key=site)
    elif state:
        response = list_agent_bridge_state_tools(
            project_root=_project_root(), workspace=_workspace_path(), site_key=site, phase=phase
        )
    else:
        response = list_agent_bridge_browser_tools(
            project_root=_project_root(), workspace=_workspace_path(), site_key=site
        )
    if json_output:
        _emit(response)
        return
    tools = response.get("tools") if isinstance(response.get("tools"), list) else []
    typer.echo(f"site={response.get('site_key') or site} tools={len(tools)}")
    for tool in tools:
        if isinstance(tool, dict):
            typer.echo(f"- {tool.get('name')}: {str(tool.get('description') or '').strip()[:160]}")


def _emit_call(*, site: str, tool: str, args: str, phase: str, turn: str, json_output: bool, legacy: bool = False, state: bool = False) -> None:
    parsed = _json_args(args)
    if state:
        response = call_agent_bridge_state_tool(project_root=_project_root(), workspace=_workspace_path(), site_key=site, tool_name=tool, arguments=parsed, turn_id=turn, phase=phase)
    elif legacy:
        response = call_browser_handoff_tool(project_root=_project_root(), workspace=_workspace_path(), site_key=site, tool_name=tool, arguments=parsed, turn_id=turn, phase=phase)
    else:
        response = call_agent_bridge_browser_tool(project_root=_project_root(), workspace=_workspace_path(), site_key=site, tool_name=tool, arguments=parsed, turn_id=turn, phase=phase)
    if json_output:
        _emit(response)
        return
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    typer.echo(f"site={response.get('site_key') or site} tool={tool} status={'ok' if result.get('ok') else 'error'} trace={result.get('trace_ref') or ''}")
    if result.get("summary"):
        typer.echo(str(result["summary"]))


@agent_bridge_app.command("browser-tools")
def agent_bridge_browser_tools(site: str = typer.Option(..., "--site"), json_output: bool = typer.Option(False, "--json")) -> None:
    _emit_tools(site=site, phase="", json_output=json_output)


@agent_bridge_app.command("browser-call")
def agent_bridge_browser_call(site: str = typer.Option(..., "--site"), tool: str = typer.Option(..., "--tool"), args: str = typer.Option("{}", "--args"), phase: str = typer.Option(AGENT_BRIDGE_STATUS, "--phase"), turn: str = typer.Option("", "--turn"), json_output: bool = typer.Option(False, "--json")) -> None:
    _emit_call(site=site, tool=tool, args=args, phase=phase, turn=turn, json_output=json_output)


@agent_bridge_app.command("state-tools")
def agent_bridge_state_tools(site: str = typer.Option(..., "--site"), phase: str = typer.Option("", "--phase"), json_output: bool = typer.Option(False, "--json")) -> None:
    _emit_tools(site=site, phase=phase, json_output=json_output, state=True)


@agent_bridge_app.command("state-call")
def agent_bridge_state_call(site: str = typer.Option(..., "--site"), tool: str = typer.Option(..., "--tool"), args: str = typer.Option("{}", "--args"), phase: str = typer.Option("", "--phase"), turn: str = typer.Option("", "--turn"), json_output: bool = typer.Option(False, "--json")) -> None:
    _emit_call(site=site, tool=tool, args=args, phase=phase, turn=turn, json_output=json_output, state=True)


@agent_bridge_app.command("phase-result")
def agent_bridge_phase_result(site: str = typer.Option(..., "--site"), status: str = typer.Option(..., "--status"), summary: str = typer.Option(..., "--summary"), phase: str = typer.Option("", "--phase"), turn: str = typer.Option("", "--turn"), json_output: bool = typer.Option(False, "--json")) -> None:
    _emit_call(site=site, tool="phase_result", args=json.dumps({"status": status, "summary": summary}), phase=phase, turn=turn, json_output=json_output, state=True)


@browser_handoff_app.command("tools")
def browser_handoff_tools(site: str = typer.Option(..., "--site"), json_output: bool = typer.Option(False, "--json")) -> None:
    _emit_tools(site=site, phase="", json_output=json_output, legacy=True)


@browser_handoff_app.command("call")
def browser_handoff_call(site: str = typer.Option(..., "--site"), tool: str = typer.Option(..., "--tool"), args: str = typer.Option("{}", "--args"), phase: str = typer.Option(AGENT_BRIDGE_STATUS, "--phase"), turn: str = typer.Option("", "--turn"), json_output: bool = typer.Option(False, "--json")) -> None:
    _emit_call(site=site, tool=tool, args=args, phase=phase, turn=turn, json_output=json_output, legacy=True)


@agent_app.command("mcp-server")
def mcp_server(project_root: str = typer.Option("", "--project-root"), workspace: str = typer.Option("", "--workspace"), transport: str = typer.Option("stdio", "--transport"), mount_path: str = typer.Option("", "--mount-path")) -> None:
    """Run the CareerEng MCP server for Codex or another local agent."""
    root = Path(project_root).expanduser().resolve() if project_root.strip() else _project_root()
    resolved_workspace = Path(workspace).expanduser().resolve() if workspace.strip() else _workspace_path()
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise typer.BadParameter("transport must be one of: stdio, sse, streamable-http")
    run_mcp_server(project_root=root, workspace=resolved_workspace, transport=transport, mount_path=mount_path.strip() or None)
