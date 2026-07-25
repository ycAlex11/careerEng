"""Apply rollbackable evolution proposals."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
import shutil
from typing import Any

from careereng.career.applications.site_store import SiteStore
from careereng.evolution.work_items import ActionCardError, ActionCardStore
from careereng.evolution.proposals import ASSISTANT_CONTEXT_TARGET, EvolutionProposalError, load_proposal
from careereng.evolution.memory_units import EvolutionMemoryStore, build_loop_evolution_memory
from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, safe_file_stem, write_json


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
        elif change_type == "run_local_overlay":
            result = _apply_run_local_overlay(
                change=change,
                workspace=workspace_path,
                proposal=proposal,
            )
            applied_files.append(result)
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
        elif change_type == "site_mode_update":
            result = _apply_site_mode_update(change=change, workspace=workspace_path, root=root, run_dir=run_dir)
            applied_files.append(result["file_record"])
            diff_chunks.append(result["diff"])
        else:
            raise EvolutionApplyError(f"Unsupported change_type at apply time: {change_type}")

    patch_path = run_dir / "applied_patch.diff"
    patch_path.write_text("\n".join(chunk for chunk in diff_chunks if chunk).rstrip() + ("\n" if diff_chunks else ""), encoding="utf-8")
    applied_files_path = run_dir / "applied_files.json"
    write_json(applied_files_path, {"applied_at": now_iso(), "files": applied_files})
    closed_run_local = _close_superseded_run_local_overlays(
        workspace=workspace_path,
        run_payload=run_payload,
        applied_files=applied_files,
    )

    now = now_iso()
    run_payload["status"] = "applied"
    run_payload["updated_at"] = now
    outputs = run_payload.setdefault("outputs", {})
    outputs["proposal"] = str(Path("proposals") / "proposal.json")
    outputs["applied_patch"] = str(patch_path)
    outputs["applied_files"] = str(applied_files_path)
    if closed_run_local.get("closed_count"):
        outputs["closed_run_local"] = closed_run_local
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
    _mark_action_card_applied(workspace=workspace_path, run_payload=run_payload, applied_files=applied_files)
    _update_summary(run_dir=run_dir, run_payload=run_payload, applied_files=applied_files)

    return {
        "run_id": run_payload.get("run_id"),
        "status": "applied",
        "run_dir": run_dir,
        "applied_count": len(applied_files),
        "applied_files": applied_files_path,
        "applied_patch": patch_path,
        "closed_run_local": closed_run_local,
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
    _validate_skill_patch_replacement(
        replacement=replacement,
        section=section,
        heading_level=heading_level,
        target=target,
    )
    updated = _replace_markdown_section(original, section=section, level=heading_level, replacement=replacement)
    if updated == original:
        return {
            "file_record": {
                "change_id": str(change.get("change_id") or ""),
                "change_type": "skill_patch",
                "target_file": str(target),
                "relative_path": str(relative_target),
                "snapshot_path": "",
                "summary": str(change.get("summary") or ""),
                "status": "skipped_noop",
            },
            "diff": "",
        }
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


def _apply_site_mode_update(*, change: dict[str, Any], workspace: Path, root: Path, run_dir: Path) -> dict[str, Any]:
    """Apply an LLM-selected site lifecycle mode with a normal file snapshot."""

    site_key = str(change.get("site_key") or "").strip()
    mode = str(change.get("mode") or "").strip().lower()
    if not site_key or mode not in {"ready", "exploration"}:
        raise EvolutionApplyError("site_mode_update requires site_key and mode=ready|exploration.")
    site_store = SiteStore(workspace)
    skill = site_store.load_skill(site_key)
    if not skill.get("exists"):
        raise EvolutionApplyError(f"site_mode_update target site Skill is missing: {site_key}")
    target = Path(skill["path"])
    original = target.read_text(encoding="utf-8")
    before_mode = str((skill.get("front_matter") or {}).get("status") or "")
    if before_mode == mode:
        return {
            "file_record": {
                "change_id": str(change.get("change_id") or ""),
                "change_type": "site_mode_update",
                "site_key": site_key,
                "mode": mode,
                "target_file": str(target),
                "relative_path": str(target.resolve().relative_to(root.resolve())),
                "snapshot_path": "",
                "summary": str(change.get("summary") or ""),
                "status": "skipped_noop",
            },
            "diff": "",
        }
    snapshot = _snapshot_file(target=target, root=root, run_dir=run_dir)
    updated_skill = site_store.set_skill_mode(site_key, mode=mode)
    updated = Path(updated_skill["path"]).read_text(encoding="utf-8")
    relative_target = target.resolve().relative_to(root.resolve())
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
            "change_type": "site_mode_update",
            "site_key": site_key,
            "mode": mode,
            "target_file": str(target),
            "relative_path": str(relative_target),
            "snapshot_path": str(snapshot),
            "summary": str(change.get("summary") or ""),
        },
        "diff": diff,
    }


def _apply_run_local_overlay(*, change: dict[str, Any], workspace: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    content = str(change.get("content") or "").strip()
    change_id = str(change.get("change_id") or make_id("change"))
    run_id_part = safe_file_stem(str(proposal.get("run_id") or "")).replace("-", "_")
    change_id_part = safe_file_stem(change_id).replace("-", "_")
    default_proposal_id = (
        f"run_local_prop_{run_id_part}_{change_id_part}"
        if run_id_part
        else f"run_local_prop_{change_id_part}_{make_id('auto')}"
    )
    proposal_id = str(change.get("proposal_id") or default_proposal_id).strip()
    pattern = str(change.get("pattern") or "manual_solution_proposal").strip()
    site_key = str(change.get("site_key") or "").strip()
    phase = str(change.get("phase") or "").strip()
    scope = str(change.get("scope") or "").strip()
    unit = build_loop_evolution_memory(
        candidate_id=str(proposal.get("candidate_id") or "site_apply_loop_control"),
        scope=scope,
        site_key=site_key,
        phase=phase,
        lifecycle="run_local",
        status="active",
        pattern=pattern,
        evidence=str(change.get("evidence") or proposal.get("diagnosis") or ""),
        summary=str(change.get("summary") or "Run-local overlay generated by an assistant solution provider."),
        avoid_patterns=change.get("avoid_patterns") if isinstance(change.get("avoid_patterns"), list) else [],
        recommended_patterns=[content],
        source={
            "provider": str(change.get("provider") or proposal.get("provider") or "assistant"),
            "run_id": str(proposal.get("run_id") or ""),
            "source_evidence_id": str(change.get("source_evidence_id") or ""),
            "action_card": str(change.get("action_card") or ""),
        },
        target=str(change.get("target_ref") or ""),
        confidence=float(change.get("confidence") or 0.6),
        proposal={
            "proposal_id": proposal_id,
            "proposal_kind": "run_local_overlay",
            "proposal_status": "materialized",
            "prompt_overlay": content,
            "expected_validation": str(
                change.get("expected_validation")
                or "The next unit should not repeat the source failure unchanged."
            ),
            "source_evidence_id": str(change.get("source_evidence_id") or ""),
            "target_ref": str(change.get("target_ref") or ""),
            "materialized_change": {
                "type": "run_local_overlay",
                "content": content,
                "source": str(change.get("provider") or proposal.get("provider") or "assistant"),
            },
        },
    )
    saved = EvolutionMemoryStore(workspace).upsert(unit)
    return {
        "change_id": change_id,
        "change_type": "run_local_overlay",
        "target_file": str(workspace / "evolution" / "memory" / "units.jsonl"),
        "relative_path": "",
        "snapshot_path": "",
        "summary": str(change.get("summary") or ""),
        "memory_id": str(saved.get("memory_id") or ""),
        "proposal_id": proposal_id,
        "scope": scope,
    }


def _close_superseded_run_local_overlays(
    *,
    workspace: Path,
    run_payload: dict[str, Any],
    applied_files: list[dict[str, Any]],
) -> dict[str, Any]:
    context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    batch_id = str(context.get("batch_id") or "").strip()
    site_key = str(context.get("site_key") or "").strip()
    phase = str(context.get("phase") or "").strip()
    if not batch_id or not site_key or not phase:
        return {"closed_count": 0, "closed_memory_ids": []}
    scope = f"batch:{batch_id}:site:{site_key}:{phase}"
    exclude_memory_ids = [
        str(row.get("memory_id") or "").strip()
        for row in applied_files
        if isinstance(row, dict) and str(row.get("memory_id") or "").strip()
    ]
    exclude_proposal_ids = [
        str(row.get("proposal_id") or "").strip()
        for row in applied_files
        if isinstance(row, dict) and str(row.get("proposal_id") or "").strip()
    ]
    return EvolutionMemoryStore(workspace).close_run_local_scope_after_synthesis(
        scope=scope,
        reason="new evolution proposal applied",
        run_id=str(run_payload.get("run_id") or ""),
        exclude_memory_ids=exclude_memory_ids,
        exclude_proposal_ids=exclude_proposal_ids,
    )


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


def _validate_skill_patch_replacement(*, replacement: str, section: str, heading_level: int, target: Path) -> None:
    text = str(replacement or "")
    if "\\n" in text:
        raise EvolutionApplyError(
            f"skill_patch replacement appears to contain literal escaped newlines instead of Markdown line breaks: {target}"
        )
    lines = text.splitlines()
    non_empty = [line.strip() for line in lines if line.strip()]
    if not non_empty:
        raise EvolutionApplyError(f"skill_patch replacement is empty: {target}")
    expected_heading = f"{'#' * max(1, int(heading_level or 1))} {section}"
    if non_empty[0] != expected_heading:
        raise EvolutionApplyError(f"skill_patch replacement must start with target heading `{expected_heading}`: {target}")
    if len(lines) < 2:
        raise EvolutionApplyError(f"skill_patch replacement must include Markdown body lines: {target}")


def _mark_action_card_applied(*, workspace: Path, run_payload: dict[str, Any], applied_files: list[dict[str, Any]]) -> None:
    context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    card_id = str(context.get("action_card_id") or "").strip()
    if not card_id:
        return
    try:
        ActionCardStore(workspace).update_card_metadata(
            card_id,
            metadata={
                "proposal_status": "applied",
                "proposal_applied_at": now_iso(),
                "solution_run_id": str(run_payload.get("run_id") or ""),
                "applied_change_types": [
                    str(row.get("change_type") or "")
                    for row in applied_files
                    if isinstance(row, dict) and str(row.get("change_type") or "")
                ],
            },
            summary="Evolution proposal applied.",
        )
    except ActionCardError:
        return


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
