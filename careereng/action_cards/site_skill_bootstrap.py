"""Action-card helpers for drafting new site AI Skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.action_cards.schema import ACTION_CARD_CODEX_DRAFT
from careereng.action_cards.store import ActionCardStore
from careereng.utils import safe_file_stem


SITE_SKILL_BOOTSTRAP_TASK = "site_skill_bootstrap"
NEW_SITE_WORKFLOW_TRANSFER_CANDIDATE_ID = "new_site_workflow_transfer"
REFERENCE_SITE_KEYS = ("amd", "microsoft", "nvidia", "qualcomm")


def create_site_skill_bootstrap_card(
    *,
    workspace: Path | str,
    project_root: Path | str,
    site_key: str,
    site_name: str,
    base_url: str,
    skill_path: Path | str,
    registry_id: str = "",
) -> dict[str, Any]:
    """Create a generic Codex card for making a new site AI Skill testable."""
    workspace_path = Path(workspace)
    root = Path(project_root)
    normalized_site_key = safe_file_stem(site_key)
    target_skill_path = _resolve_project_path(root, skill_path)
    related_files = _related_files(
        root=root,
        workspace=workspace_path,
        site_key=normalized_site_key,
        target_skill_path=target_skill_path,
    )
    reference_site_keys = _reference_site_keys(root=root, target_site_key=normalized_site_key)
    return ActionCardStore(workspace_path).create_card(
        card_type=ACTION_CARD_CODEX_DRAFT,
        title=f"Make site AI Skill testable for {site_name or normalized_site_key}",
        goal=(
            "Draft or refine the target site AI Skill so the site can be tested through "
            "session_preparation, application_status_review, channel_discovery, job_filtering, "
            "and job_retrieval before apply is enabled."
        ),
        reason=(
            "A registered site has only a new site AI Skill. Codex should use existing site "
            "patterns and local project rules to make it testable without adding Python "
            "browser-action semantics."
        ),
        source_type="site_registration",
        source_id=registry_id or normalized_site_key,
        source_ref=str(target_skill_path),
        priority="medium",
        related_files=related_files,
        suggested_actions=[
            "Read the linked evidence pack before editing the target site AI Skill.",
            "Read the target site AI Skill first.",
            "Read the New Site Workflow Transfer candidate spec for the completion and safety boundary.",
            "Use the project jobs Skill and mature site AI Skills as examples, not as code to copy blindly.",
            "Use `workspace/profile/application_profile.md` as the canonical source for reusable application-form facts.",
            "Keep site-specific navigation, filtering, login, review, and retrieval behavior in the site AI Skill.",
            "Define success and stop conditions for session preparation, application-status review, and job retrieval.",
            "Keep apply_enabled false until the user explicitly approves apply behavior for this site.",
        ],
        safety_notes=[
            "Do not add local browser-action DSLs or Python selector semantics.",
            "Do not weaken login, MFA, CAPTCHA, or final-submit safety boundaries.",
            "Do not enable apply unless the user explicitly asks for that site.",
        ],
        done_when=[
            "The target site AI Skill has concrete instructions for login readiness, status review, and retrieval.",
            "The first test target is retrieval/status review, not automatic apply.",
            "The action card is closed with the refined changes or a reason it cannot be refined yet.",
        ],
        metadata={
            "task": SITE_SKILL_BOOTSTRAP_TASK,
            "candidate_id": NEW_SITE_WORKFLOW_TRANSFER_CANDIDATE_ID,
            "target_kind": "ai_skill",
            "site_key": normalized_site_key,
            "site_name": str(site_name or normalized_site_key),
            "base_url": str(base_url or ""),
            "target_skill": str(target_skill_path),
            "evolution_run_id": "",
            "evidence_pack": "",
            "reference_site_keys": reference_site_keys,
            "initial_test_scope": [
                "session_preparation",
                "application_status_review",
                "channel_discovery",
                "job_filtering",
                "job_retrieval",
            ],
            "apply_enabled_policy": "keep_false_until_user_approval",
        },
        semantic_tags=[
            "site_skill",
            "new_site",
            "workflow_transfer",
            "codex_draft",
            "browser_workflow",
            "non_apply_test",
        ],
        dedupe_key=f"{ACTION_CARD_CODEX_DRAFT}:{SITE_SKILL_BOOTSTRAP_TASK}:{normalized_site_key}",
    )


def _resolve_project_path(root: Path, path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _related_files(*, root: Path, workspace: Path, site_key: str, target_skill_path: Path) -> list[str]:
    files: list[Path] = [
        target_skill_path,
        root / "skills" / "search" / "jobs" / "SKILL.md",
        workspace / "profile" / "application_profile.md",
        root / "docs" / "action_cards" / "README.md",
        root / "docs" / "evolution" / "candidates" / "new_site_workflow_transfer.md",
        workspace / "sites" / site_key / "site.json",
        workspace / "sites" / site_key / "events" / "all.jsonl",
    ]
    for ref_path in _reference_site_skill_paths(root=root, target_site_key=site_key):
        files.append(ref_path)
    return [str(path) for path in files if path.exists()]


def _reference_site_keys(*, root: Path, target_site_key: str) -> list[str]:
    keys: list[str] = []
    for path in _reference_site_skill_paths(root=root, target_site_key=target_site_key):
        site_key = path.parent.name
        if site_key not in keys:
            keys.append(site_key)
    return keys


def _reference_site_skill_paths(*, root: Path, target_site_key: str) -> list[Path]:
    sites_dir = root / "skills" / "search" / "jobs" / "sites"
    if not sites_dir.exists():
        return []
    found = {path.parent.name: path for path in sites_dir.glob("*/SKILL.md") if path.parent.name != target_site_key}
    ordered: list[Path] = []
    for key in REFERENCE_SITE_KEYS:
        path = found.pop(key, None)
        if path is not None:
            ordered.append(path)
    ordered.extend(path for _key, path in sorted(found.items()))
    return ordered[:8]
