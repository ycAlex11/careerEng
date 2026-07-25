"""Proposal loading and validation for evolution runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.artifacts import EvolutionProposalArtifactStore

ASSISTANT_CONTEXT_TARGET = "docs/assistant_bridge/CODEX_CONTEXT.md"
SUPPORTED_CHANGE_TYPES = {
    "skill_patch",
    "run_local_overlay",
    "routing_example_append",
    "memory_unit_append",
    "assistant_context_update",
    "site_mode_update",
}
FORBIDDEN_CHANGE_TYPES = {
    "python_code_patch",
    "config_patch",
    "provider_patch",
    "mcp_patch",
    "browser_protocol_patch",
    "security_patch",
    "storage_schema_migration",
    "final_submit_policy_patch",
    "shell_command",
}
REQUIRED_PROPOSAL_FIELDS = ("run_id", "candidate_id", "diagnosis", "proposed_changes")


class EvolutionProposalError(ValueError):
    """Raised when an evolution proposal is invalid."""


def proposal_path_for_run(run_dir: Path | str) -> Path:
    return EvolutionProposalArtifactStore().proposal_path(run_dir)


def load_proposal(run_dir: Path | str) -> dict[str, Any]:
    artifact_store = EvolutionProposalArtifactStore()
    path = artifact_store.proposal_path(run_dir)
    try:
        payload = artifact_store.load_json(run_dir)
    except FileNotFoundError as exc:
        raise EvolutionProposalError(f"Missing proposal: {path}") from exc
    except ValueError as exc:
        raise EvolutionProposalError(f"Invalid proposal JSON: {path}") from exc
    validate_proposal(payload)
    return payload


def validate_proposal(payload: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_PROPOSAL_FIELDS if payload.get(field) in (None, "", [])]
    if missing:
        raise EvolutionProposalError(f"Proposal missing required field(s): {', '.join(missing)}")
    changes = payload.get("proposed_changes")
    if not isinstance(changes, list) or not changes:
        raise EvolutionProposalError("Proposal must include at least one proposed change.")
    for idx, change in enumerate(changes, start=1):
        if not isinstance(change, dict):
            raise EvolutionProposalError(f"Change #{idx} must be an object.")
        _validate_change(change, idx=idx)


def _validate_change(change: dict[str, Any], *, idx: int) -> None:
    change_type = str(change.get("change_type") or "").strip()
    if not change_type:
        raise EvolutionProposalError(f"Change #{idx} missing change_type.")
    if change_type in FORBIDDEN_CHANGE_TYPES:
        raise EvolutionProposalError(f"Change #{idx} uses forbidden change_type: {change_type}")
    if change_type not in SUPPORTED_CHANGE_TYPES:
        raise EvolutionProposalError(f"Change #{idx} uses unsupported change_type: {change_type}")
    if change_type == "skill_patch":
        _require(change, idx=idx, fields=("target_file", "target_section", "replacement_markdown"))
        if str(change.get("patch_strategy") or "replace_section") != "replace_section":
            raise EvolutionProposalError(f"Change #{idx} skill_patch only supports patch_strategy=replace_section.")
    elif change_type == "run_local_overlay":
        _require(change, idx=idx, fields=("scope", "site_key", "phase", "content"))
    elif change_type in {"routing_example_append", "memory_unit_append"}:
        row = change.get("row")
        if not isinstance(row, dict) or not row:
            raise EvolutionProposalError(f"Change #{idx} {change_type} requires non-empty row object.")
    elif change_type == "assistant_context_update":
        _require(change, idx=idx, fields=("target_file", "content_markdown"))
        if str(change.get("target_file") or "").strip() != ASSISTANT_CONTEXT_TARGET:
            raise EvolutionProposalError(
                f"Change #{idx} assistant_context_update can only target {ASSISTANT_CONTEXT_TARGET}."
            )
    elif change_type == "site_mode_update":
        _require(change, idx=idx, fields=("site_key", "mode"))
        if str(change.get("mode") or "").strip().lower() not in {"ready", "exploration"}:
            raise EvolutionProposalError(f"Change #{idx} site_mode_update mode must be ready or exploration.")


def _require(change: dict[str, Any], *, idx: int, fields: tuple[str, ...]) -> None:
    missing = [field for field in fields if not str(change.get(field) or "").strip()]
    if missing:
        raise EvolutionProposalError(f"Change #{idx} missing required field(s): {', '.join(missing)}")
