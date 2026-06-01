"""Thin new-site bootstrap launcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.action_cards import ActionCardStore, NEW_SITE_WORKFLOW_TRANSFER_CANDIDATE_ID
from careereng.evolution import create_evolution_run


def bootstrap_site(
    *,
    site_name: str,
    base_url: str,
    session_id: str,
    turn_id: str,
    search_store: Any,
    site_tools: Any,
    channel_locator: Any,
) -> dict[str, Any]:
    """Prepare a new or draft site for Codex-assisted site AI Skill drafting.

    This function intentionally does not run browser phases. It reuses site
    registration, URL discovery, skill-template creation, and action-card
    creation so Codex can take over from the generated card.
    """
    name = str(site_name or "").strip()
    if not name:
        raise ValueError("site_name is required")

    existing = site_tools.site_store.find_site(name)
    existing_url = str(existing.get("base_url") or "").strip() if isinstance(existing, dict) else ""
    existing_source = str(existing.get("source_type") or "").strip() if isinstance(existing, dict) else ""
    resolved_url = str(base_url or "").strip()
    url_source = "input" if resolved_url else ""
    query_id = ""

    if not resolved_url and existing_url:
        resolved_url = existing_url
        url_source = "site_registry"

    if not resolved_url:
        query = search_store.start_query(
            session_id=session_id,
            turn_id=turn_id,
            user_message=f"site bootstrap {name}",
            query_spec={"mode": "site_bootstrap", "company": name},
        )
        query_id = str(query.get("query_id") or "")
        resolved = channel_locator.resolve_company_apply_channels(
            query_id=query_id,
            companies=[{"company": name, "base_url": ""}],
        )
        if resolved:
            resolved_url = str(resolved[0].get("base_url") or "").strip()
            if resolved_url:
                url_source = str(resolved[0].get("channel_source") or "locator")

    if not url_source:
        url_source = "missing"

    result = site_tools.handle_site_request(
        site_name=name,
        base_url=resolved_url,
        apply_requested=False,
        session_id=session_id,
        turn_id=turn_id,
        source_type=existing_source or "bootstrap",
    )
    site_id = str(result.get("site_id") or "")
    project_root = Path(site_tools.project_root or site_tools.site_store.project_root)
    workspace = Path(site_tools.site_store.workspace)
    action_card_id = str(result.get("action_card_id") or "")
    evolution_run: dict[str, Any] = {}
    if action_card_id:
        evolution_run = create_evolution_run(
            project_root=project_root,
            workspace=workspace,
            candidate_id=NEW_SITE_WORKFLOW_TRANSFER_CANDIDATE_ID,
            context={
                "site_key": site_id,
                "site_name": str(result.get("site_name") or name),
                "base_url": resolved_url,
                "base_url_source": url_source,
                "target_skill": str(result.get("skill_path") or ""),
                "action_card_id": action_card_id,
                "initial_test_scope": [
                    "session_preparation",
                    "application_status_review",
                    "channel_discovery",
                    "job_filtering",
                    "job_retrieval",
                ],
                "apply_enabled_policy": "keep_false_until_user_approval",
            },
        )
        ActionCardStore(workspace).update_card_metadata(
            action_card_id,
            metadata={
                "candidate_id": NEW_SITE_WORKFLOW_TRANSFER_CANDIDATE_ID,
                "evolution_run_id": str(evolution_run.get("run_id") or ""),
                "evidence_pack": str(evolution_run.get("evidence_pack") or ""),
            },
            related_files=[
                str(evolution_run.get("run_json") or ""),
                str(evolution_run.get("evidence_pack") or ""),
                str(evolution_run.get("summary") or ""),
            ],
            commands=[
                f"python -m careereng action-card show {action_card_id}",
                f"python -m careereng evolution candidate-show {NEW_SITE_WORKFLOW_TRANSFER_CANDIDATE_ID}",
            ],
            summary="Linked site bootstrap action card to evolution run evidence.",
        )
    if site_id:
        site_tools.site_store.append_event(
            site_id,
            "site.bootstrap.prepared",
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "site_name": name,
                "base_url": resolved_url,
                "base_url_source": url_source,
                "query_id": query_id,
                "action_card_id": result.get("action_card_id") or "",
                "evolution_run_id": evolution_run.get("run_id") or "",
                "evidence_pack": str(evolution_run.get("evidence_pack") or ""),
                "skill_path": result.get("skill_path") or "",
            },
        )
    return {
        **result,
        "base_url_source": url_source,
        "query_id": query_id,
        "evolution_run_id": str(evolution_run.get("run_id") or ""),
        "evidence_pack": str(evolution_run.get("evidence_pack") or ""),
        "evolution_run_dir": str(evolution_run.get("run_dir") or ""),
        "next_action": (
            f"python -m careereng action-card show {result.get('action_card_id')}"
            if result.get("action_card_id")
            else ""
        ),
    }
