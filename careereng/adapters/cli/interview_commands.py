"""CLI adapter for interview records and local audio capture."""

from __future__ import annotations

import json

import typer

from careereng.adapters.bootstrap import project_root_from_cwd, workspace_path
from careereng.career.interviews import (
    InterviewStore,
    InterviewStoreError,
    build_interview_summary,
    render_interview_summary,
    save_interview_candidates,
)
from careereng.career.interviews.capture import (
    AudioCaptureDependencyError,
    capture_audio_chunks,
    list_audio_devices,
)


interview_app = typer.Typer(help="Interview preparation and transcript records")
capture_app = typer.Typer(help="Local capture commands")
capture_audio_app = typer.Typer(help="Audio capture commands")
capture_app.add_typer(capture_audio_app, name="audio")


def _workspace_path():
    return workspace_path(project_root_from_cwd())


def _csv_list(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


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
def capture_audio_devices() -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
    """Show one interview session summary."""
    try:
        summary = build_interview_summary(_interview_store(), session_id, recent_limit=recent_limit)
    except InterviewStoreError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(render_interview_summary(summary).rstrip())


@interview_app.command("audio-devices")
def interview_audio_devices() -> None:
    """List audio devices for interview capture."""
    _print_audio_devices()


@interview_app.command("capture-audio")
def interview_capture_audio(
    session_id: str = typer.Argument(..., help="Interview session ID"),
    device: str = typer.Option("", "--device", help="Input device index or name"),
    sample_rate: int = typer.Option(16000, "--sample-rate", help="Recording sample rate"),
    channels: int = typer.Option(1, "--channels", min=1, help="Input channel count"),
) -> None:
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
