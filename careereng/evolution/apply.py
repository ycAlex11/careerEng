"""Apply rollbackable evolution proposals."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
import shutil
from typing import Any

from careereng.evolution.proposals import ASSISTANT_CONTEXT_TARGET, EvolutionProposalError, load_proposal
from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json


class EvolutionApplyError(ValueError):
    """Raised when an evolution proposal cannot be applied safely."""


def apply_evolution_run(*, workspace: Path | str, run_id: str, project_root: Path | str) -> dict[str, Any]:
    workspace_path = Path(workspace)
    root = Path(project_root)
    run_dir = workspace_path / "evolution" / "runs" / str(run_id or "").strip()
    if not run_dir.exists():
        raise EvolutionApplyError(f"Unknown evolution run: {run_id}")
    run_path = run_dir / "run.json"
    run_payload = read_json(run_path)
    if not run_payload:
        raise EvolutionApplyError(f"Missing run.json for evolution run: {run_id}")
    proposal = load_proposal(run_dir)
    if str(proposal.get("run_id") or "") != str(run_payload.get("run_id") or ""):
        raise EvolutionApplyError("Proposal run_id does not match run.json.")
    if str(proposal.get("candidate_id") or "") != str(run_payload.get("candidate_id") or ""):
        raise EvolutionApplyError("Proposal candidate_id does not match run.json.")

    applied_files: list[dict[str, Any]] = []
    diff_chunks: list[str] = []
    for change in proposal.get("proposed_changes") or []:
        change_type = str(change.get("change_type") or "")
        if change_type == "skill_patch":
            result = _apply_skill_patch(change=change, root=root, run_dir=run_dir)
            applied_files.append(result["file_record"])
            diff_chunks.append(result["diff"])
        elif change_type == "routing_example_append":
            result = _append_jsonl_change(
                workspace_path / "assistant_bridge" / "routing_examples.jsonl",
                row=change.get("row") if isinstance(change.get("row"), dict) else {},
                id_field="routing_example_id",
                id_prefix="aroute_example",
                change=change,
            )
            applied_files.append(result)
        elif change_type == "memory_unit_append":
            result = _append_jsonl_change(
                workspace_path / "evolution" / "memory" / "units.jsonl",
                row=change.get("row") if isinstance(change.get("row"), dict) else {},
                id_field="memory_id",
                id_prefix="memory",
                change=change,
            )
            applied_files.append(result)
        elif change_type == "assistant_context_update":
            result = _apply_assistant_context_update(change=change, root=root, run_dir=run_dir)
            applied_files.append(result["file_record"])
            diff_chunks.append(result["diff"])
        else:
            raise EvolutionApplyError(f"Unsupported change_type at apply time: {change_type}")

    patch_path = run_dir / "applied_patch.diff"
    patch_path.write_text("\n".join(chunk for chunk in diff_chunks if chunk).rstrip() + ("\n" if diff_chunks else ""), encoding="utf-8")
    applied_files_path = run_dir / "applied_files.json"
    write_json(applied_files_path, {"applied_at": now_iso(), "files": applied_files})

    now = now_iso()
    run_payload["status"] = "applied"
    run_payload["updated_at"] = now
    outputs = run_payload.setdefault("outputs", {})
    outputs["proposal"] = str(Path("proposals") / "proposal.json")
    outputs["applied_patch"] = str(patch_path)
    outputs["applied_files"] = str(applied_files_path)
    lifecycle = run_payload.setdefault("lifecycle", [])
    if isinstance(lifecycle, list):
        lifecycle.append({"status": "applied", "at": now, "summary": f"Applied {len(applied_files)} proposal change(s)."})
    rollback = run_payload.setdefault("rollback", {})
    rollback["available"] = any(bool(row.get("snapshot_path")) for row in applied_files)
    rollback["snapshot_dir"] = str(run_dir / "snapshots")
    evaluation = run_payload.setdefault("evaluation", {})
    evaluation["status"] = "pending"
    selection = run_payload.setdefault("selection", {})
    selection["status"] = "pending_evaluation"
    write_json(run_path, run_payload)
    _update_summary(run_dir=run_dir, run_payload=run_payload, applied_files=applied_files)

    return {
        "run_id": run_payload.get("run_id"),
        "status": "applied",
        "run_dir": run_dir,
        "applied_count": len(applied_files),
        "applied_files": applied_files_path,
        "applied_patch": patch_path,
        "summary": run_dir / "summary.md",
    }


def _apply_skill_patch(*, change: dict[str, Any], root: Path, run_dir: Path) -> dict[str, Any]:
    target = _safe_project_path(root, str(change.get("target_file") or ""))
    root_resolved = root.resolve()
    relative_target = target.resolve().relative_to(root_resolved)
    if target.suffix.lower() not in {".md", ".markdown"}:
        raise EvolutionApplyError(f"skill_patch target must be Markdown: {target}")
    original = target.read_text(encoding="utf-8")
    replacement = str(change.get("replacement_markdown") or "").rstrip() + "\n"
    section = str(change.get("target_section") or "").strip()
    heading_level = int(change.get("heading_level") or 2)
    updated = _replace_markdown_section(original, section=section, level=heading_level, replacement=replacement)
    if updated == original:
        raise EvolutionApplyError(f"skill_patch produced no change: {target}")
    snapshot = _snapshot_file(target=target, root=root, run_dir=run_dir)
    target.write_text(updated, encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{relative_target}",
            tofile=f"b/{relative_target}",
        )
    )
    return {
        "file_record": {
            "change_id": str(change.get("change_id") or ""),
            "change_type": "skill_patch",
            "target_file": str(target),
            "relative_path": str(relative_target),
            "snapshot_path": str(snapshot),
            "summary": str(change.get("summary") or ""),
        },
        "diff": diff,
    }


def _apply_assistant_context_update(*, change: dict[str, Any], root: Path, run_dir: Path) -> dict[str, Any]:
    target_text = str(change.get("target_file") or "").strip()
    if target_text != ASSISTANT_CONTEXT_TARGET:
        raise EvolutionApplyError(f"assistant_context_update can only target {ASSISTANT_CONTEXT_TARGET}.")
    target = _safe_project_path(root, target_text)
    relative_target = target.resolve().relative_to(root.resolve())
    if target.suffix.lower() not in {".md", ".markdown"}:
        raise EvolutionApplyError(f"assistant_context_update target must be Markdown: {target}")
    original = target.read_text(encoding="utf-8")
    updated = str(change.get("content_markdown") or "").rstrip() + "\n"
    if updated == original:
        raise EvolutionApplyError(f"assistant_context_update produced no change: {target}")
    snapshot = _snapshot_file(target=target, root=root, run_dir=run_dir)
    target.write_text(updated, encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{relative_target}",
            tofile=f"b/{relative_target}",
        )
    )
    return {
        "file_record": {
            "change_id": str(change.get("change_id") or ""),
            "change_type": "assistant_context_update",
            "target_file": str(target),
            "relative_path": str(relative_target),
            "snapshot_path": str(snapshot),
            "summary": str(change.get("summary") or ""),
        },
        "diff": diff,
    }


def _append_jsonl_change(path: Path, *, row: dict[str, Any], id_field: str, id_prefix: str, change: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.setdefault(id_field, make_id(id_prefix))
    payload.setdefault("created_at", now_iso())
    payload.setdefault("status", payload.get("status") or "candidate")
    payload.setdefault("source", "evolution")
    payload.setdefault("evolution_change_id", str(change.get("change_id") or ""))
    JSONLStore(path).append(payload)
    return {
        "change_id": str(change.get("change_id") or ""),
        "change_type": str(change.get("change_type") or ""),
        "target_file": str(path),
        "relative_path": "",
        "snapshot_path": "",
        "summary": str(change.get("summary") or ""),
        "appended_id": str(payload.get(id_field) or ""),
    }


def _safe_project_path(root: Path, relative_path: str) -> Path:
    if not relative_path.strip():
        raise EvolutionApplyError("target_file is required.")
    target = (root / relative_path).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise EvolutionApplyError(f"target_file is outside project root: {relative_path}") from exc
    if not target.exists():
        raise EvolutionApplyError(f"target_file does not exist: {target}")
    return target


def _snapshot_file(*, target: Path, root: Path, run_dir: Path) -> Path:
    relative = target.resolve().relative_to(root.resolve())
    snapshot = run_dir / "snapshots" / "before" / relative
    ensure_dir(snapshot.parent)
    shutil.copy2(target, snapshot)
    return snapshot


def _replace_markdown_section(text: str, *, section: str, level: int, replacement: str) -> str:
    heading = f"{'#' * max(1, int(level))} {section}"
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx
            break
    if start is None:
        raise EvolutionApplyError(f"Markdown section not found: {heading}")
    boundary_prefix = "#" * max(1, int(level))
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].lstrip()
        if stripped.startswith("#"):
            hash_count = len(stripped) - len(stripped.lstrip("#"))
            if hash_count <= len(boundary_prefix) and stripped[hash_count : hash_count + 1] == " ":
                end = idx
                break
    replacement_text = replacement if replacement.endswith("\n") else replacement + "\n"
    return "".join([*lines[:start], replacement_text, *lines[end:]])


def _update_summary(*, run_dir: Path, run_payload: dict[str, Any], applied_files: list[dict[str, Any]]) -> None:
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
        "## Applied Changes",
    ]
    for row in applied_files:
        lines.append(f"- `{row.get('change_type')}` {row.get('summary') or row.get('target_file')}")
    lines.extend(
        [
            "",
            "## Current Stage",
            "",
            "The proposal has been applied and archived. Evaluation is pending.",
            "",
            "## Next Expected Stage",
            "",
            "Run evaluation after enough follow-up evidence exists, then select accepted, rejected, rollback, or keep_observing.",
        ]
    )
    (run_dir / "summary.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
