"""Rollback applied evolution runs from archived snapshots."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from careereng.utils import ensure_dir, now_iso, read_json, write_json


class EvolutionRollbackError(ValueError):
    """Raised when an evolution run cannot be rolled back safely."""


def rollback_evolution_run(
    *,
    workspace: Path | str,
    run_id: str,
    project_root: Path | str,
    reason: str = "",
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    root = Path(project_root)
    run_dir = workspace_path / "evolution" / "runs" / str(run_id or "").strip()
    run_path = run_dir / "run.json"
    run_payload = read_json(run_path)
    if not run_payload:
        raise EvolutionRollbackError(f"Unknown evolution run: {run_id}")
    if str(run_payload.get("status") or "") == "rolled_back":
        raise EvolutionRollbackError("Evolution run has already been rolled back.")

    applied_files_path = _resolve_run_path(run_dir, run_payload.get("outputs", {}).get("applied_files"))
    applied_payload = read_json(applied_files_path) if applied_files_path else {}
    records = applied_payload.get("files") if isinstance(applied_payload.get("files"), list) else []
    if not records:
        raise EvolutionRollbackError("No applied file records found for rollback.")

    restored: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        snapshot_text = str(row.get("snapshot_path") or "").strip()
        if not snapshot_text:
            skipped.append(
                {
                    "change_id": str(row.get("change_id") or ""),
                    "change_type": str(row.get("change_type") or ""),
                    "reason": "append-only or no snapshot",
                }
            )
            continue
        snapshot = _resolve_run_path(run_dir, snapshot_text)
        if not snapshot.exists():
            raise EvolutionRollbackError(f"Rollback snapshot is missing: {snapshot}")
        target = _target_path(root=root, row=row)
        ensure_dir(target.parent)
        shutil.copy2(snapshot, target)
        restored.append(
            {
                "change_id": str(row.get("change_id") or ""),
                "change_type": str(row.get("change_type") or ""),
                "target_file": str(target),
                "snapshot_path": str(snapshot),
            }
        )

    if not restored:
        raise EvolutionRollbackError("No rollbackable snapshot records were found.")

    now = now_iso()
    rollback_payload: dict[str, Any] = {
        "run_id": run_payload.get("run_id"),
        "rolled_back_at": now,
        "reason": str(reason or "").strip() or "manual rollback",
        "restored_files": restored,
        "skipped_changes": skipped,
    }
    retention_dir = ensure_dir(run_dir / "retention")
    rollback_path = retention_dir / "rollback.json"
    write_json(rollback_path, rollback_payload)

    run_payload["status"] = "rolled_back"
    run_payload["updated_at"] = now
    outputs = run_payload.setdefault("outputs", {})
    outputs["rollback"] = str(rollback_path)
    rollback_state = run_payload.setdefault("rollback", {})
    rollback_state.update(
        {
            "available": False,
            "restored_at": now,
            "reason": rollback_payload["reason"],
            "restored_count": len(restored),
            "skipped_count": len(skipped),
        }
    )
    selection = run_payload.setdefault("selection", {})
    selection.update(
        {
            "status": "rolled_back",
            "decision_at": now,
            "reason": rollback_payload["reason"],
        }
    )
    lifecycle = run_payload.setdefault("lifecycle", [])
    if isinstance(lifecycle, list):
        lifecycle.append(
            {
                "status": "rolled_back",
                "at": now,
                "summary": f"Restored {len(restored)} file(s) from evolution snapshots.",
            }
        )
    write_json(run_path, run_payload)
    _update_summary(run_dir=run_dir, run_payload=run_payload, rollback=rollback_payload)

    return {
        "run_id": run_payload.get("run_id"),
        "status": "rolled_back",
        "restored_count": len(restored),
        "skipped_count": len(skipped),
        "rollback": rollback_path,
        "summary": run_dir / "summary.md",
    }


def _target_path(*, root: Path, row: dict[str, Any]) -> Path:
    relative = str(row.get("relative_path") or "").strip()
    if relative:
        target = (root / relative).resolve()
    else:
        target = Path(str(row.get("target_file") or "")).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise EvolutionRollbackError(f"Rollback target is outside project root: {target}") from exc
    return target


def _resolve_run_path(run_dir: Path, value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        return Path()
    path = Path(text)
    if path.is_absolute():
        return path
    return run_dir / path


def _update_summary(*, run_dir: Path, run_payload: dict[str, Any], rollback: dict[str, Any]) -> None:
    candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
    lines = [
        "# Evolution Run Summary",
        "",
        f"- Run ID: `{run_payload.get('run_id')}`",
        f"- Status: `{run_payload.get('status')}`",
        f"- Candidate: `{run_payload.get('candidate_id')}`",
        f"- Target: `{candidate.get('target_ref') or ''}`",
        f"- Risk: `{candidate.get('risk_level') or ''}`",
        f"- Apply Policy: `{candidate.get('apply_policy') or ''}`",
        "",
        "## Rollback",
        "",
        f"- Restored Files: `{len(rollback.get('restored_files') or [])}`",
        f"- Skipped Changes: `{len(rollback.get('skipped_changes') or [])}`",
        f"- Reason: {rollback.get('reason')}",
        "",
        "## Current Stage",
        "",
        "The applied proposal has been rolled back from archived snapshots.",
    ]
    (run_dir / "summary.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
