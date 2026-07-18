"""CLI adapter for evolution work items and review operations."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from careereng.config.loader import load_config
from careereng.adapters.bootstrap import build_loop
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
from careereng.evolution.outer_loop import BatchEvolutionOrchestrator
from careereng.evolution.work_items import ActionCardError, ActionCardStore


action_card_app = typer.Typer(help="Action card review tasks")
evolution_cli_app = typer.Typer(help="Evolution commands")
evolution_cli_app.add_typer(action_card_app, name="action-card")


def _project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "careereng").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def _workspace_path() -> Path:
    workspace = load_config(_project_root()).paths.workspace_path(_project_root())
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _print_lines(lines: list[str]) -> None:
    typer.echo("\n".join(lines))


def _close_loop(loop: object) -> None:
    close = getattr(loop, "close", None)
    if callable(close):
        close()


@action_card_app.command("list")
def action_card_list(
    status: str = typer.Option("open", "--status", help="open/done/cancelled/all"),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum cards to show"),
) -> None:
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
def action_card_show(card_id: str = typer.Argument(..., help="Action card ID")) -> None:
    """Show one action card as Markdown."""
    try:
        typer.echo(ActionCardStore(_workspace_path()).markdown_text(card_id).rstrip())
    except ActionCardError as exc:
        raise typer.BadParameter(str(exc)) from exc


@action_card_app.command("close")
def action_card_close(
    card_id: str = typer.Argument(..., help="Action card ID"),
    result: str = typer.Option("", "--result", help="Review or execution result summary"),
) -> None:
    """Mark an action card as done."""
    try:
        card = ActionCardStore(_workspace_path()).close_card(card_id, result_summary=result)
    except ActionCardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"closed={card.get('card_id')} status={card.get('status')} "
        f"markdown={_workspace_path() / str(card.get('markdown_path') or '')}"
    )


@action_card_app.command("cancel")
def action_card_cancel(
    card_id: str = typer.Argument(..., help="Action card ID"),
    reason: str = typer.Option("", "--reason", help="Cancellation reason"),
) -> None:
    """Cancel an action card."""
    try:
        card = ActionCardStore(_workspace_path()).cancel_card(card_id, reason=reason)
    except ActionCardError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"cancelled={card.get('card_id')} status={card.get('status')} "
        f"markdown={_workspace_path() / str(card.get('markdown_path') or '')}"
    )


@evolution_cli_app.command("review")
def evolution_review(
    max_evidence: int = typer.Option(200, "--max-evidence", min=1),
) -> None:
    """Build an evidence-backed evolution review and context pack."""
    workspace = _workspace_path()
    review = build_evolution_review(workspace=workspace, project_root=_project_root(), max_evidence=max_evidence)
    paths = save_evolution_review(review, workspace=workspace)
    _print_lines(
        [
            "Evolution Review",
            f"- evidence: {review.get('evidence_count', 0):,}",
            f"- open candidates: {review.get('candidate_count', 0):,}",
            f"- memory units: {review.get('memory_count', 0):,}",
            f"- review: {paths['review_markdown']}",
            f"- review_json: {paths['review_json']}",
            f"- context: {paths['context_markdown']}",
            f"- candidates: {paths['open_candidates_store']}",
        ]
    )


@evolution_cli_app.command("candidates")
def evolution_candidates() -> None:
    """List available evolution candidate specs."""
    specs = load_candidate_specs(_project_root())
    if not specs:
        typer.echo("No evolution candidate specs found.")
        return
    for spec in specs:
        typer.echo(f"{spec.id}\t{spec.risk_level}\t{spec.target_type}\t{spec.target_ref}")


@evolution_cli_app.command("lessons")
def evolution_lessons(
    status: str = typer.Option("accepted", "--status"),
    site: str = typer.Option("", "--site"),
    phase: str = typer.Option("", "--phase"),
    limit: int = typer.Option(20, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List durable browser-control lessons used by evolution."""
    rows = BrowserControlLessonStore(_workspace_path()).list(
        status=status, site_key=site, phase=phase, limit=limit
    )
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(render_lessons_markdown(rows, limit=limit).rstrip())


@evolution_cli_app.command("candidate-show")
def evolution_candidate_show(
    candidate_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show one evolution candidate spec."""
    try:
        spec = get_candidate_spec(_project_root(), candidate_id)
    except CandidateSpecError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return
    _print_lines(
        [
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
    )


@evolution_cli_app.command("run")
def evolution_run(candidate: str = typer.Option(..., "--candidate", "-c")) -> None:
    """Create an archived evolution run and evidence pack for a candidate."""
    try:
        result = create_evolution_run(
            project_root=_project_root(), workspace=_workspace_path(), candidate_id=candidate
        )
    except CandidateSpecError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_lines(
        [
            f"run={result['run_id']} status={result['status']}",
            f"candidate={result['candidate_id']}",
            f"run_dir={result['run_dir']}",
            f"run_json={result['run_json']}",
            f"evidence_pack={result['evidence_pack']}",
            f"summary={result['summary']}",
        ]
    )


@evolution_cli_app.command("solution")
def evolution_solution(
    card: str = typer.Option("", "--card"),
    run: str = typer.Option("", "--run"),
    candidate: str = typer.Option("", "--candidate", "-c"),
) -> None:
    """Create a Codex-readable solution request and proposal output path."""
    if bool(card.strip()) == bool(run.strip()):
        raise typer.BadParameter("Pass exactly one of --card or --run.")
    try:
        result = (
            create_solution_request_for_action_card(
                project_root=_project_root(), workspace=_workspace_path(), card_id=card.strip(), candidate_id=candidate
            )
            if card.strip()
            else create_solution_request_for_run(
                project_root=_project_root(), workspace=_workspace_path(), run_id=run.strip()
            )
        )
    except EvolutionSolutionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_lines(
        [
            f"run={result['run_id']} status={result['status']}",
            f"candidate={result['candidate_id']}",
            f"action_card={result.get('action_card_id') or ''}",
            f"solution_request={result['solution_request']}",
            f"proposal_output={result['proposal_output_path']}",
            f"apply_command=python -m careereng evolution apply --run {result['run_id']}",
        ]
    )


@evolution_cli_app.command("apply")
def evolution_apply(run: str = typer.Option(..., "--run")) -> None:
    """Apply a rollbackable proposal from an evolution run archive."""
    try:
        result = apply_evolution_run(workspace=_workspace_path(), project_root=_project_root(), run_id=run)
    except (EvolutionApplyError, EvolutionProposalError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_lines(
        [
            f"run={result['run_id']} status={result['status']}",
            f"applied_count={result['applied_count']}",
            f"applied_files={result['applied_files']}",
            f"applied_patch={result['applied_patch']}",
            f"summary={result['summary']}",
        ]
    )


@evolution_cli_app.command("pending-solution")
def evolution_pending_solution(
    site: str = typer.Option("", "--site"),
    batch: str = typer.Option("", "--batch"),
    limit: int = typer.Option(5, "--limit", min=1),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show pending evolution solution requests for Codex or another assistant."""
    rows = list_pending_solution_requests(
        workspace=_workspace_path(), site_key=site, batch_id=batch, limit=limit
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
    _print_lines(lines)


@evolution_cli_app.command("continue-batch")
def evolution_continue_batch(
    batch: str = typer.Option(..., "--batch"), site: str = typer.Option("", "--site")
) -> None:
    """Apply a written proposal if present, then continue the outer batch loop."""
    rows = list_pending_solution_requests(workspace=_workspace_path(), site_key=site, batch_id=batch, limit=1)
    lines: list[str] = []
    if rows:
        row = rows[0]
        if not bool(row.get("proposal_exists")):
            _print_lines(
                [
                    f"batch={batch} status=waiting_solution next_action=write_proposal",
                    f"run={row['run_id']}",
                    f"solution_request={row['solution_request']}",
                    f"proposal_output={row['proposal_output_path']}",
                    f"apply_command={row['apply_command']}",
                    f"continue_command=python -m careereng evolution continue-batch --batch {batch}",
                ]
            )
            return
        try:
            applied = apply_evolution_run(
                workspace=_workspace_path(), project_root=_project_root(), run_id=str(row.get("run_id") or "")
            )
        except (EvolutionApplyError, EvolutionProposalError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        lines.extend([f"applied_run={applied['run_id']} status={applied['status']}", f"applied_count={applied['applied_count']}"])
    loop, _ = build_loop(project_root=_project_root(), workspace=_workspace_path())
    try:
        reply = BatchEvolutionOrchestrator(loop.job_flow).continue_after_solution(batch)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        _close_loop(loop)
    if lines:
        lines.append("")
    lines.append(reply)
    _print_lines(lines)


@evolution_cli_app.command("evaluate")
def evolution_evaluate(
    run: str = typer.Option(..., "--run"), recent_limit: int = typer.Option(10, "--recent-limit", min=1)
) -> None:
    """Evaluate an applied run, or generate a review pack for review-only runs."""
    try:
        result = evaluate_evolution_run(
            workspace=_workspace_path(), project_root=_project_root(), run_id=run, recent_limit=recent_limit
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
    _print_lines(lines)


@evolution_cli_app.command("rollback")
def evolution_rollback(run: str = typer.Option(..., "--run"), reason: str = typer.Option("", "--reason")) -> None:
    """Rollback an applied evolution run from archived snapshots."""
    try:
        result = rollback_evolution_run(
            workspace=_workspace_path(), project_root=_project_root(), run_id=run, reason=reason
        )
    except EvolutionRollbackError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_lines(
        [
            f"run={result['run_id']} status={result['status']}",
            f"restored_count={result['restored_count']}",
            f"skipped_count={result['skipped_count']}",
            f"rollback={result['rollback']}",
            f"summary={result['summary']}",
        ]
    )


@evolution_cli_app.command("trigger-scan")
def evolution_trigger_scan(
    status: str = typer.Option("active", "--status"),
    create_runs: bool = typer.Option(True, "--create-runs/--no-create-runs"),
    review_gate: bool = typer.Option(False, "--review-gate/--no-review-gate"),
) -> None:
    """Scan local evidence and create evolution triggers."""
    try:
        result = scan_evolution_triggers(
            project_root=_project_root(),
            workspace=_workspace_path(),
            status="" if status.strip().lower() == "all" else status.strip(),
            create_runs=create_runs,
            review_gate=review_gate,
        )
    except EvolutionTriggerError as exc:
        raise typer.BadParameter(str(exc)) from exc
    lines = [f"triggered={result['triggered_count']}", f"review_gate={str(review_gate).lower()}"]
    for label, group in (
        ("site_workflow", result.get("site_workflow") or {}),
        ("target_company_intelligence", result.get("target_company_intelligence") or {}),
        ("application_strategy", result.get("application_strategy") or {}),
        ("assistant_router_memory_intake", result.get("assistant_router_memory_intake") or {}),
    ):
        if not isinstance(group, dict):
            continue
        lines.extend(
            [
                f"{label}: candidate={group.get('candidate_id')} triggered={group.get('triggered_count')} buckets={group.get('bucket_count')} sites={group.get('site_count')}",
                f"{label}: state={group.get('state_path')}",
                f"{label}: open_candidates={group.get('open_candidates_path')}",
            ]
        )
        for row in group.get("triggered") or []:
            if isinstance(row, dict):
                subject = row.get("site_key") or row.get("area") or row.get("candidate_id") or label
                detail = row.get("phase") or row.get("area") or row.get("trigger_type")
                count = row.get("phase_run_count") or row.get("job_count") or row.get("review_count") or row.get("rejected_count") or 0
                lines.append(f"- {subject}:{detail} trigger={row.get('trigger_type')} count={count} run={row.get('evolution_run_id') or '-'} review_card={row.get('evolution_review_card_id') or '-'}")
    _print_lines(lines)
