"""Discover pending evolution solution work orders.

This module is deliberately orchestration-only. It exposes the next concrete
solution request for Codex or another assistant; it does not infer workflow
strategy or generate proposal content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.proposals import proposal_path_for_run
from careereng.utils import read_json


PENDING_SOLUTION_STATUSES = {"waiting_solution"}


def list_pending_solution_requests(
    *,
    workspace: Path | str,
    site_key: str = "",
    batch_id: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return pending solution requests, newest first."""

    workspace_path = Path(workspace)
    runs_dir = workspace_path / "evolution" / "runs"
    normalized_site = str(site_key or "").strip()
    normalized_batch = str(batch_id or "").strip()
    rows: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return rows
    for run_json in runs_dir.glob("*/run.json"):
        run_dir = run_json.parent
        payload = read_json(run_json)
        if not payload:
            continue
        status = str(payload.get("status") or "").strip()
        if status not in PENDING_SOLUTION_STATUSES:
            continue
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        if normalized_site and str(context.get("site_key") or "") != normalized_site:
            continue
        if normalized_batch and str(context.get("batch_id") or "") != normalized_batch:
            continue
        output = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
        request_path = _resolve_output_path(run_dir, output.get("solution_request") or "solution_request.md")
        proposal_path = _resolve_output_path(run_dir, output.get("proposal") or proposal_path_for_run(run_dir))
        proposal_exists = proposal_path.exists()
        rows.append(
            {
                "run_id": str(payload.get("run_id") or run_dir.name),
                "status": status,
                "candidate_id": str(payload.get("candidate_id") or ""),
                "site_key": str(context.get("site_key") or ""),
                "phase": str(context.get("phase") or ""),
                "batch_id": str(context.get("batch_id") or ""),
                "failure_pattern": str(context.get("failure_pattern") or ""),
                "action_card_id": str(context.get("action_card_id") or ""),
                "target_ref": str(context.get("target_ref") or ""),
                "updated_at": str(payload.get("updated_at") or payload.get("created_at") or ""),
                "created_at": str(payload.get("created_at") or ""),
                "run_dir": str(run_dir),
                "run_json": str(run_json),
                "solution_request": str(request_path),
                "proposal_output_path": str(proposal_path),
                "proposal_exists": proposal_exists,
                "next_action": "apply_proposal" if proposal_exists else "write_proposal",
                "apply_command": f"python -m careereng evolution apply --run {payload.get('run_id') or run_dir.name}",
            }
        )
    rows.sort(key=lambda row: (str(row.get("updated_at") or ""), str(row.get("run_id") or "")), reverse=True)
    if limit <= 0:
        return rows
    return rows[:limit]


def latest_pending_solution_request(
    *,
    workspace: Path | str,
    site_key: str = "",
    batch_id: str = "",
) -> dict[str, Any]:
    rows = list_pending_solution_requests(workspace=workspace, site_key=site_key, batch_id=batch_id, limit=1)
    return rows[0] if rows else {}


def _resolve_output_path(run_dir: Path, value: Any) -> Path:
    if isinstance(value, Path):
        path = value
    else:
        path = Path(str(value or ""))
    if path.is_absolute():
        return path
    return run_dir / path
