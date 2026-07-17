"""Action-card helpers for refining site Skills after browser workflow failures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.work_items.schema import ACTION_CARD_CODEX_DEBUG
from careereng.evolution.work_items.store import ActionCardStore
from careereng.utils import safe_file_stem


SITE_SKILL_REFINEMENT_TASK = "site_skill_refinement"
SITE_WORKFLOW_COMPACTION_CANDIDATE_ID = "site_workflow_compaction"


def create_site_skill_refinement_card(
    *,
    workspace: Path | str,
    project_root: Path | str,
    site_key: str,
    site_name: str,
    phase: str,
    batch_id: str,
    reason_tag: str,
    summary: str,
    current_url: str = "",
    trace_ref: str = "",
    skill_path: Path | str = "",
    workflow_memory_path: Path | str = "",
    failure_snapshot_path: Path | str = "",
) -> dict[str, Any]:
    """Create or refresh a Codex debug card for site Skill refinement."""
    workspace_path = Path(workspace)
    root = Path(project_root)
    normalized_site_key = safe_file_stem(site_key)
    normalized_phase = safe_file_stem(phase)
    target_skill_path = _resolve_project_path(root, skill_path) if str(skill_path or "").strip() else root / "skills" / "search" / "jobs" / "sites" / normalized_site_key / "SKILL.md"
    related_files = _related_files(
        root=root,
        workspace=workspace_path,
        site_key=normalized_site_key,
        target_skill_path=target_skill_path,
        batch_id=batch_id,
        trace_ref=trace_ref,
        workflow_memory_path=workflow_memory_path,
        failure_snapshot_path=failure_snapshot_path,
    )
    dedupe_key = f"{ACTION_CARD_CODEX_DEBUG}:{SITE_SKILL_REFINEMENT_TASK}:{normalized_site_key}:{normalized_phase}"
    store = ActionCardStore(workspace_path)
    metadata = {
        "task": SITE_SKILL_REFINEMENT_TASK,
        "candidate_id": SITE_WORKFLOW_COMPACTION_CANDIDATE_ID,
        "target_kind": "ai_skill",
        "site_key": normalized_site_key,
        "site_name": str(site_name or normalized_site_key),
        "phase": str(phase or ""),
        "batch_id": str(batch_id or ""),
        "reason_tag": str(reason_tag or ""),
        "current_url": str(current_url or ""),
        "trace_ref": str(trace_ref or ""),
        "target_skill": str(target_skill_path),
        "workflow_memory": str(_display_path(workspace_path, workflow_memory_path)),
        "failure_snapshot": str(_display_path(workspace_path, failure_snapshot_path)),
        "browser_control_lessons": _related_lessons_file(workspace_path),
        "expected_outcome": (
            "Codex inspects the Skill, workflow memory, failed batch, trace, and failure snapshot, infers the missing "
            "site-specific workflow step, then refines the site Skill without adding Python browser-action semantics."
        ),
    }
    commands = ["python -m careereng evolution candidate-show site_workflow_compaction"]
    existing = store.find_by_dedupe_key(dedupe_key)
    if existing:
        return store.update_card_metadata(
            str(existing.get("card_id") or ""),
            metadata=metadata,
            related_files=related_files,
            commands=commands,
            summary=f"Updated site Skill refinement evidence for {normalized_site_key}:{phase}.",
        )

    return store.create_card(
        card_type=ACTION_CARD_CODEX_DEBUG,
        title=f"Refine {site_name or normalized_site_key} site Skill for {phase}",
        goal=(
            "Use the failed browser workflow evidence to infer the missing site-specific operation and refine "
            "the site AI Skill so the next run avoids the same failure and reaches a clean phase outcome."
        ),
        reason=(
            f"Phase `{phase}` failed for `{normalized_site_key}` with `{reason_tag}`. "
            f"{str(summary or '').strip()}"
        ).strip(),
        source_type="browser_phase",
        source_id=str(batch_id or normalized_site_key),
        source_ref=str(target_skill_path),
        priority="high" if str(reason_tag or "").strip() else "medium",
        related_files=related_files,
        suggested_actions=[
            "Read the workflow memory first to understand previous successful and failed strategies.",
            "Read accepted browser-control lessons before editing; treat them as durable evolution knowledge.",
            "Inspect the failed batch, latest trace, and failure snapshot before editing the site Skill.",
            "Infer the missing or wrong site-specific workflow step from the evidence instead of only summarizing the failure.",
            "When stable site-visible labels or options are present, write them explicitly into the site Skill.",
            "Use `workspace/profile/application_profile.md` for reusable application-form facts instead of copying those facts into every site Skill.",
            "Update only the site Skill guidance unless the evidence clearly belongs in the project Skill.",
            "Make the next expected phase behavior explicit, including stop conditions.",
            "Keep apply_enabled false unless the user explicitly approved apply behavior for this site.",
        ],
        commands=commands,
        safety_notes=[
            "Do not add Python browser-action semantics or selector DSLs.",
            "Do not weaken login, MFA, CAPTCHA, or final-submit safety boundaries.",
            "Treat snapshots as evidence for Skill wording, not as permission to hard-code browser actions.",
        ],
        done_when=[
            "The site Skill reflects the observed failure, the inferred missing workflow step, and a concrete next strategy.",
            "The change keeps apply safety unchanged.",
            "The next test run has enough guidance to avoid repeating the same failure.",
        ],
        metadata=metadata,
        semantic_tags=[
            "site_skill",
            "workflow_refinement",
            "codex_debug",
            "browser_workflow",
            "phase_failure",
            normalized_phase,
        ],
        dedupe_key=dedupe_key,
    )


def _related_files(
    *,
    root: Path,
    workspace: Path,
    site_key: str,
    target_skill_path: Path,
    batch_id: str,
    trace_ref: str,
    workflow_memory_path: Path | str,
    failure_snapshot_path: Path | str,
) -> list[str]:
    files: list[Path] = [
        target_skill_path,
        root / "skills" / "search" / "jobs" / "SKILL.md",
        workspace / "profile" / "application_profile.md",
        root / "docs" / "evolution" / "candidates" / "site_workflow_compaction.md",
        root / "docs" / "action_cards" / "README.md",
        workspace / "sites" / site_key / "site.json",
        workspace / "sites" / site_key / "events" / "all.jsonl",
        workspace / "evolution" / "browser_control" / "phase_events.jsonl",
    ]
    lessons = _related_lessons_file(workspace)
    if lessons:
        files.append(Path(lessons))
    if batch_id:
        files.append(workspace / "jobs" / "batches" / f"{batch_id}.json")
    if trace_ref:
        files.append(workspace / trace_ref)
    for raw in (workflow_memory_path, failure_snapshot_path):
        resolved = _resolve_workspace_path(workspace, raw)
        if resolved:
            files.append(resolved)
    return [str(path) for path in files if path.exists()]


def _resolve_project_path(root: Path, path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _resolve_workspace_path(workspace: Path, path: Path | str) -> Path | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return workspace / candidate


def _display_path(workspace: Path, path: Path | str) -> str:
    resolved = _resolve_workspace_path(workspace, path)
    if resolved is None:
        return ""
    try:
        return str(resolved.relative_to(workspace))
    except ValueError:
        return str(resolved)


def _related_lessons_file(workspace: Path | str) -> str:
    from careereng.evolution.browser_control.lessons import related_lessons_file

    return related_lessons_file(workspace)
