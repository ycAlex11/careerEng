"""Codex work item for the outer synthesis of a completed exploration run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.work_items.schema import ACTION_CARD_CODEX_REVIEW
from careereng.evolution.work_items.store import ActionCardStore
from careereng.utils import safe_file_stem


SITE_EXPLORATION_SYNTHESIS_TASK = "site_exploration_synthesis"
SITE_WORKFLOW_COMPACTION_CANDIDATE_ID = "site_workflow_compaction"


def create_site_exploration_synthesis_card(
    *,
    workspace: Path | str,
    project_root: Path | str,
    site_key: str,
    site_name: str,
    batch_id: str,
    skill_path: Path | str,
    cycle_outcome: str = "",
    batch_report_path: Path | str = "",
    workflow_summary_path: Path | str = "",
) -> dict[str, Any]:
    """Create one durable Codex review card for a completed exploration unit.

    This helper only indexes durable evidence. It deliberately does not infer
    whether the site is ready, which workflow rule should change, or whether a
    further exploration run is necessary.
    """

    workspace_path = Path(workspace)
    root = Path(project_root)
    normalized_site_key = safe_file_stem(site_key)
    target_skill = _resolve_project_path(root, skill_path)
    related_files = _related_files(
        root=root,
        workspace=workspace_path,
        site_key=normalized_site_key,
        target_skill=target_skill,
        batch_id=batch_id,
        batch_report_path=batch_report_path,
        workflow_summary_path=workflow_summary_path,
    )
    metadata = {
        "task": SITE_EXPLORATION_SYNTHESIS_TASK,
        "candidate_id": SITE_WORKFLOW_COMPACTION_CANDIDATE_ID,
        # Solution runs resolve a candidate *specification*, not the display
        # label stored in candidate_id. Keep the outer card compatible with
        # the same proposal pipeline used by item-loop cards.
        "candidate_spec_id": SITE_WORKFLOW_COMPACTION_CANDIDATE_ID,
        "target_kind": "ai_skill",
        "site_key": normalized_site_key,
        "site_name": str(site_name or normalized_site_key),
        "batch_id": str(batch_id or ""),
        "target_skill": str(target_skill),
        "execution_mode": "exploration",
        "cycle_outcome": str(cycle_outcome or ""),
        "proposal_contract": {
            "required_decision": "site_mode_update",
            "allowed_modes": ["ready", "exploration"],
            "ready_meaning": (
                "At least one application completed successfully in this exploration cycle, every required phase completed, "
                "and no unresolved blocker or user-required information remains."
            ),
            "exploration_meaning": "More evidence or a durable Skill/lesson change is required before normal execution.",
            "cycle_outcome": str(cycle_outcome or ""),
        },
    }
    store = ActionCardStore(workspace_path)
    card = store.create_card(
        card_type=ACTION_CARD_CODEX_REVIEW,
        title=f"Synthesize {site_name or normalized_site_key} exploration result",
        goal=(
            "Inspect the completed exploration batch, traces, snapshots, and current Skill; then decide whether the site "
            "is ready for normal execution or should remain in exploration."
        ),
        reason=(
            "The inner exploration run reached a terminal batch result. Codex must synthesize the evidence before any "
            "site-readiness decision is persisted."
        ),
        source_type="exploration_batch",
        source_id=str(batch_id or normalized_site_key),
        source_ref=str(target_skill),
        priority="high",
        related_files=related_files,
        suggested_actions=[
            "Read the target site Skill and the full batch report before deciding.",
            "Inspect relevant traces and snapshots, including both successful and failed units.",
            "Use the evidence pack to decide whether a durable Skill/lesson change is needed.",
            (
                "Write `site_mode_update` with `mode: ready` only when the evidence shows at least one successful application, "
                "all required phases completed, and no unresolved blocker or user-required information."
            ),
            "Use the recorded cycle outcome when selecting `ready` or `exploration`; do not invent a follow-up command.",
        ],
        safety_notes=[
            "Python records and applies the decision but must not decide site readiness.",
            "Do not add site-specific browser behavior to Python.",
            "Keep final-submit and user-authentication boundaries unchanged.",
        ],
        done_when=[
            "A valid evidence-backed proposal is written and applied.",
            "The proposal contains an explicit `site_mode_update` decision for this site.",
            "Any required Skill or lesson changes are included as supported proposal changes.",
        ],
        metadata=metadata,
        semantic_tags=["exploration", "outer_synthesis", "site_skill", "codex_review"],
        dedupe_key=f"{ACTION_CARD_CODEX_REVIEW}:{SITE_EXPLORATION_SYNTHESIS_TASK}:{normalized_site_key}:{batch_id}",
    )
    # A prior failed handoff may already have created this deduplicated card
    # without the required spec ID. Merge the current contract before retrying.
    return store.update_card_metadata(
        str(card.get("card_id") or ""),
        metadata=metadata,
        summary="Ensured exploration synthesis candidate contract.",
    )


def _resolve_project_path(root: Path, value: Path | str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _related_files(
    *,
    root: Path,
    workspace: Path,
    site_key: str,
    target_skill: Path,
    batch_id: str,
    batch_report_path: Path | str,
    workflow_summary_path: Path | str,
) -> list[str]:
    files = [
        target_skill,
        root / "skills" / "search" / "jobs" / "SKILL.md",
        root / "docs" / "evolution" / "candidates" / "site_workflow_compaction.md",
        workspace / "jobs" / "batches" / f"{batch_id}.json",
        workspace / "jobs" / "apply_plans" / batch_id / f"{site_key}.json",
        workspace / "sites" / site_key / "events" / "all.jsonl",
        workspace / "profile" / "application_profile.md",
    ]
    for value in (batch_report_path, workflow_summary_path):
        if str(value or "").strip():
            path = Path(value)
            files.append(path if path.is_absolute() else workspace / path)
    return [str(path) for path in files if path.exists()]
